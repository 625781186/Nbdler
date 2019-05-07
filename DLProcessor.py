

import socket, ssl
import threading
import time
from .DLInfos import Target
import logging
import traceback

import sys

if sys.version_info <= (2, 7):
    from urllib import splitvalue, splitquery, urlencode

elif sys.version_info >= (3, 0):
    from urllib.parse import splitvalue, splitquery, urlencode


logger = logging.getLogger('nbdler')


TMP_BUFFER_SIZE = 1024 * 1024 * 1

socket.setdefaulttimeout(3)
ssl._create_default_https_context = ssl._create_unverified_context

class OpaReq:
    def __init__(self):
        self.cut = []
        self.pause = False
        self.switch = False
        self.wait = 0

    def clear(self):
        self.cut = []
        self.pause = False
        self.switch = False
        self.wait = 0


class ErrorCounter(object):

    _404__THRESHOLD = 5
    _302__THRESHOLD = 20

    RECV_TIMEOUT_THRESHOLD = 10
    SOCKET_ERROR_THRESHOLD = 10

    def __init__(self):
        self._404_ = 0
        self._302_ = 0

        self.recv_error = 0
        self.socket_error = 0

        self.error_occur = False

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)
        if key != 'error_occur':
            for i, j in self.check().items():
                if getattr(self, i, 0) > getattr(ErrorCounter, j, 0):
                    self.error_occur = True
                    break
            else:
                self.error_occur = False

    def isError(self):
        return self.error_occur

    def clear(self):
        self._404_ = self._302_ = self.recv_error = self.socket_error = 0

    def check(self):
        return {
            '_404_': '_404__THRESHOLD',
            '_302_': '_302__THRESHOLD',
            'recv_error': 'RECV_TIMEOUT_THRESHOLD',
            'socket_error': 'SOCKET_ERROR_THRESHOLD',
        }


class Processor(object):
    def __init__(self, Progress, Urlid):
        self.progress = Progress

        self.url = None
        self.urlid = Urlid

        self.buff = []
        self.buff_inc = 0
        self.opareq = OpaReq()

        self.target = Target()

        self.__thread__ = None

        self.__opa_lock__ = threading.Lock()
        self.__run_lock__ = threading.Lock()
        self.__buff_lock__ = threading.Lock()

        self.error_counter = ErrorCounter()

    def _Thread(self, *args, **kwargs):
        return self.getHandler().thrpool.Thread(*args, **kwargs)

    def loadUrl(self, Urlid):

        urls = self.getHandler().url.getAllUrl()

        if Urlid in urls:
            self.url = urls[Urlid]
            self.target.load(self.url.url)
        else:
            self.url = None

        self.urlid = Urlid

    def isReady(self):
        return self.progress.isReady()

    def isRunning(self):
        return self.__thread__ and self.__thread__._started.is_set() and self.__thread__.isAlive()

    def isPause(self):
        return self.progress.isPause()

    def isEnd(self):
        return self.progress.isEnd()

    def isGoEnd(self):
        return self.progress.isGoEnd()

    def getHandler(self):
        return self.progress.globalprog.handler

    def selfCheck(self):

        if self.opareq.pause:
            self.getPause()
            return False

        if not self.url:
            self.loadUrl(self.urlid)

        if not self.url or not self.getHandler().url.hasUrl(self.urlid):
            self.getSwitch()

        if self.isReady():
            if not self.isRunning():
                if self.error_counter.isError():
                    self.getSwitch()

                if self.opareq.cut:
                    self.getCut()

                if self.opareq.pause:
                    self.getPause()
                    return False

                return True
        else:
            self.close()

        return False

    def run(self):
        with self.__run_lock__:
            if self.selfCheck():
                thr = self._Thread(target=self.__getdata__, name='Nbdler-Processor')
                self.__thread__ = thr
                thr.start()

    def __getdata__(self):
        if self.opareq.cut:
            self.getCut()

        if self.opareq.pause:
            self.getPause()
            return

        sock, buff = self.makeSocket()

        if not sock:

            self.error_counter.socket_error += 1
            time.sleep(0.5)
            self.run()
            return
        else:

            status, _headers = parse_headers(buff[:(buff.index(b'\r\n\r\n'))])
            self.target.update(headers=_headers, code=status)

            if status == 200:
                self.__200__(sock, buff)
            elif status == 206:
                self.__206__(sock, buff)
            elif status == 302:
                self.__302__(sock)
            elif status == 405:
                self.__405__(sock)
            elif status == 404:
                self.__404__(sock)
            # elif status == 416:
            #     self.__416__(sock)
            elif status != 206 and status != 200:
                self.__404__(sock)

            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except socket.error:
                pass


    def makeSocket(self):
        sock = None
        buff = b''

        try:
            ip = socket.gethostbyname(self.target.host)

            if self.target.protocol == 'https':
                sock = ssl.wrap_socket(socket.socket())

                # ssl.SSLError: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure
                sock.server_hostname = self.target.host
            elif self.target.protocol == 'http':
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            assert sock is not None
            if not sock:
                self.error_counter.socket_error += 1
                return None, b''

            sock.connect((ip, self.target.port))

            packet = self.makeSocketPacket()

            sock.send(packet)
            buff = sock.recv(1024)
        except Exception as e:
            # print(e.args)
            # traceback.print_exc()
            self.error_counter.socket_error += 1
            sock = None
        else:
            if not buff:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
                sock = None
            else:
                while b'\r\n\r\n' not in buff:
                    buff += sock.recv(512)
                    if b'HTTP' not in buff:
                        sock.shutdown(socket.SHUT_RDWR)
                        sock.close()
                        sock = None
                        break

        return sock, buff

    def __302__(self, sock):
        if self.target.headers.get('location', None):
            self.target.load(self.target.headers.get('location'))

        self.run()

    def __206__(self, sock, buff):
        self.__200__(sock, buff)

    def __200__(self, sock, buff):
        self.error_counter.clear()

        buff = buff[(buff.index(b'\r\n\r\n') + 4):]

        self.progress.go(len(buff))
        self.__recv_loop__(sock, buff)

    def __405__(self, sock):
        self.getSwitch()
        time.sleep(0.1)
        self.run()

    def __404__(self, sock):
        self.error_counter._404_ += 1
        if self.error_counter._404_ > 3:
            self.url.reload()

        time.sleep(0.3)
        self.run()

    def __other__(self, sock):
        pass


    def __416__(self, sock):
        self.error_counter._404_ += 1
        self.progress.go_in = 0
        self.progress.done_inc = 0
        time.sleep(0.1)
        self.run()


    def __recv_loop__(self, sock, buff):
        while True:
            if self.opareq.cut:
                self.getCut()

            if self.opareq.pause:
                self.buffer(buff)
                self.getPause()
                break

            # if self.opareq.wait:
            #     self.getWait()

            last_len = len(buff)
            rest = self.progress.length - self.progress.go_inc
            try:
                if rest == 0:
                    if len(buff) != 0:
                        self.buffer(buff)
                        buff = []
                        self.close()
                    break
                elif rest < 4096:
                    buff += sock.recv(rest)
                else:
                    buff += sock.recv(4096)
            except:

                self.error_counter.recv_error += 1
                self.buffer(buff[:last_len])
                return

            if len(buff) == last_len:
                self.error_counter.recv_error += 1
                if len(buff) != 0:
                    self.buffer(buff)
                return

            if len(buff) - last_len > rest:
                self.error_counter.recv_error += 1
                return

            self.progress.go(len(buff) - last_len)

            if self.progress.go_inc >= self.progress.length:
                self.buffer(buff[:self.progress.length - self.progress.done_inc - self.buff_inc])
                self.close()
                break
            elif len(buff) >= TMP_BUFFER_SIZE:
                self.buffer(buff)
                buff = b''



    def close(self):
        self.progress.globalprog.checkAllGoEnd()
        self.opareq.clear()


    def pause(self):
        self.opareq.pause = True

    def getPause(self):
        self.progress.status.pause()
        self.opareq.pause = False

    def makeSocketPacket(self):

        range_format = self.url.range_format
        Range = (self.progress.begin + self.progress.go_inc, self.progress.end)

        add_headers = {
            'Host': self.target.host,
            'Connection': 'keep-alive',
        }

        if range_format[0] == '&':
            path, query = splitquery(self.target.path)
            query_dict = extract_query(query)
            range_format = range_format % Range
            for i in range_format[1:].split('&'):
                param_key, param_value = splitvalue(i)
                query_dict[param_key] = param_value

            new_query = urlencode(query_dict)
            http_head_top = 'GET %s HTTP/1.1\r\n' % ('%s?%s' % (path, new_query))

            packet = http_head_top + '%s\r\n\r\n'
            add_headers = {
                'Host': self.target.host,
                'Connection': 'keep-alive'
            }

        else:
            http_head_top = 'GET %s HTTP/1.1\r\n' % self.target.path

            packet = http_head_top + '%s\r\n\r\n'
            range_field = range_format % Range
            key_value = [i.strip() for i in range_field.split(':')]

            key = key_value[0]
            value = key_value[1]

            add_headers[key] = value
            add_headers['Accept-Ranges'] = 'bytes'

        request_headers = dict(self.url.headers.items())
        request_headers.update(add_headers)
        request_headers_str = []
        for i in request_headers.items():
            request_headers_str.append(': '.join(i))

        packet = packet % '\r\n'.join(request_headers_str)

        return str.encode(str(packet))


    def getWait(self):
        time.sleep(self.opareq.wait)

    def getSwitch(self):

        next_urlid = self.getHandler().url.getNextId(self.urlid)
        self.loadUrl(next_urlid)

        self.error_counter.clear()

    def buffer(self, buff):
        with self.__buff_lock__:
            self.buff.append(buff)
            self.buff_inc += len(buff)

            self.progress.globalprog.checkBuffer(len(buff))

    def clearBuffer(self):
        self.buff = []
        self.buff_inc = 0

    def releaseBuffer(self, f):
        with self.__buff_lock__:
            f.seek(self.progress.begin + self.progress.done_inc)
            total_buff = 0
            for block in self.buff:
                f.write(block)
                total_buff += len(block)
            self.progress.done(total_buff)

            self.clearBuffer()


    def cutRequest(self, Range):

        last_range = [self.progress.begin, self.progress.end]

        self.opareq.cut = [Range[0], Range[1]]

        while True:
            if self.isEnd() or ((self.isReady() and not self.isRunning() and
                    not self.getHandler().thrpool.getThreadsFromName('Nbdler-SelfCheck')) or \
                    not self.opareq.cut):
                break

            time.sleep(0.1)

        return [self.progress.end, last_range[1]] if last_range[1] != self.progress.end else []


    def getCut(self):
        while self.progress.begin + self.progress.go_inc >= self.opareq.cut[0]:
            self.opareq.cut[0] += self.progress.globalprog.handler.file.BLOCK_SIZE

        if self.opareq.cut[0] >= self.opareq.cut[1]:
            retrange = []
        else:
            retrange = self.opareq.cut

        if retrange:
            self.progress.globalprog.cut(self.progress, retrange)


        self.opareq.cut = []


    # def __str__(self):
    #     return



def parse_headers(http_msg):

    http_msg = bytes.decode(http_msg)
    status_bar = http_msg[:http_msg.index('\r\n') + 2]
    status = int(status_bar.split(' ')[1])

    header = http_msg[http_msg.index('\r\n') + 2:]

    res_headers = []

    for i in header.split('\r\n'):
        if i:
            name = i[:i.index(':')].lower().strip()
            value = i[i.index(':') + 1:].lstrip()
            res_headers.append((name, value))

    return status, res_headers



def extract_query(query_str):
    querys = {}
    if query_str:
        for i in query_str.split('&'):
            key_value = splitvalue(i)
            querys[key_value[0]] = key_value[1]

    return querys