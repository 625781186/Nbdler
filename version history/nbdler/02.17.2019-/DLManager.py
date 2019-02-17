

from packer import Packer
import time,threading

class Manager(object, Packer):
    def __init__(self):
        self.tasks = {}

        self.id_map = []
        self.name_id = {}

        # self.order = []
        self.max_task = 2

        self.queue = TaskQueue()

        self.__inspector_thread__ = None

    def __inspector__(self):

        while True:
            tmp = self.queue.run[:]
            for i in tmp:
                if self.tasks[i].isEnd():
                    self.tasks[i].close()
                    self.queue.run.remove(i)
                    self.queue.done.append(i)

            self.run()

            if not self.queue.undone and self.isEnd():
                break
            time.sleep(1)


    def getHandler(self, name=None, id=None):
        if name is None and id is None:
            return None
        if id is not None:
            return self.tasks[id]

        if name is not None:
            return self.tasks[self.getIdFromName(name)]

    def getAllTask(self):
        return self.tasks

    def getRunQueue(self):
        return self.queue.run

    def getPauseQueue(self):
        return self.queue.pause

    def getDoneQueue(self):
        return self.queue.done

    def getUndoneQueue(self):
        return self.queue.undone


    def getIdFromName(self, name):
        return self.name_id[name]


    def getNameFromId(self, id):
        for i, j in self.name_id.items():
            if id == j:
                return i

    def addHandler(self, Handler, name=None):
        id = self.newId()

        name = id if not name else name

        self.tasks[id] = Handler
        self.name_id[name] = id

        self.id_map[id] = True
        # self.order.append(id)
        self.queue.undone.append(id)

        return id

    def newId(self):
        for i, j in enumerate(self.id_map):
            if not j:
                return i
        else:
            self.id_map.append(False)
            return len(self.id_map) - 1

    def remove(self, id):
        del self.tasks[id]
        self.id_map[id] = False
        del self.name_id[self.getIdFromName(id)]

    def run(self, id=None):
        if id is not None:
            if len(self.queue.run) < self.max_task:
                self.tasks[id].run()
        else:
            tmp = self.queue.undone[:]
            for i in tmp:
                if len(self.queue.run) < self.max_task:
                    if not self.tasks[i].isEnd():
                        self.tasks[i].run()
                        self.queue.run.append(i)
                        if i in self.queue.pause:
                            self.queue.pause.remove(i)
                        self.queue.undone.remove(i)

        if not self.__inspector_thread__ or not self.__inspector_thread__.isAlive():
            self.__inspector_thread__ = threading.Thread(target=self.__inspector__)
            self.__inspector_thread__.start()

    def pause(self, id=None):
        if id is not None:
            self.tasks[id].pause()
            if id in self.queue.run:
                self.queue.run.remove(id)
            if id not in self.queue.pause:
                self.queue.pause.append(id)
        else:
            for i in self.queue.run:
                self.tasks[i].pause()
                if i in self.queue.run:
                    self.queue.run.remove(i)
                if i not in self.queue.pause:
                    self.queue.pause.append(i)

    def close(self):
        pass


    def getAvgSpeed(self, id=None):
        if id is not None:
            return self.tasks[id].getAvgSpeed()

        speed = 0
        for i in self.queue.run:
            if not self.tasks[i].isEnd():
                speed += self.tasks[i].getAvgSpeed()

        return speed

    def getInsSpeed(self, id=None):
        if id is not None:
            return self.tasks[id].getInsSpeed()

        speed = 0
        for i in self.queue.run:
            if not self.tasks[i].isEnd():
                speed += self.tasks[i].getInsSpeed()
        return speed


    def getLeft(self, id=None):
        if id is not None:
            return self.tasks[id].getLeft()

        left = 0
        for i in self.queue.run:
            if not self.tasks[i].isEnd():
                left += self.tasks[i].getLeft()
        return left


    def isEnd(self, id=None):
        if id is not None:
            return self.tasks[id].isEnd()

        for i in self.queue.run:
            if not self.tasks[i].isEnd():
                break
        else:
            return True if not self.queue.undone else False
        return False

    def config(self, **kwargs):
        for i, j in self.__config_params__():
            if kwargs.has_key(i):
                setattr(self, j, kwargs[i])

    def __config_params__(self):
        return [('max_task', 'max_task')]


    def __packet_params__(self):
        return ['tasks', 'max_task']


    def unpack(self, packet):
        Packer.unpack(self, packet)


class TaskQueue(object):
    def __init__(self):
        self.run = []
        self.pause = []
        self.undone = []
        self.done = []


