#!/usr/bin/python2.7
'''
Created on January 22, 2013
(Sybrand: based on code I wrote Feb 20, 2012)

@author: Sybrand Strauss
'''

import sqlite3
import config
import logging


class SyncInfo(object):
    def __init__(self, hash, dateModified):
        self._hash = hash
        self._dateModified = dateModified

    @property
    def hash(self):
        return self._hash

    @property
    def dateModified(self):
        return self._dateModified


class StateStore(object):

    def __init__(self):
        c = config.Config()
        self.databasePath = c.get_database_path()
        logging.debug('database path = %r' % self.databasePath)
        conn = self.getConnection()
        #conn.execute('create table if not exists container '
        #             '(path)')
        conn.execute('create table if not exists object'
                     '(path, hash, datemodified)')

    def getConnection(self):
        return sqlite3.connect(self.databasePath)

    def markObjectAsSynced(self, path, objectHash, dateModified):
        logging.info('mark %s with hash %s modified '
                     '@ %s as synced' %
                     (path, objectHash, dateModified))
        conn = self.getConnection()
        c = conn.cursor()

        t = (path,)
        c.execute('''select hash from object where path = ?''', t)
        data = c.fetchone()
        if (data is None):
            t = (path, objectHash, dateModified)
            c.execute('''insert into object
                      (path, hash, dateModified)
                      values (?,?,?)''', t)
        else:
            t = (objectHash, dateModified, path)
            c.execute('''update object set hash = ?, datemodified = ?
                      where path = ?''', t)
        conn.commit()
        c.close()

    def getObjectSyncInfo(self, path):
        conn = self.getConnection()
        c = conn.cursor()
        t = (path,)
        c.execute('''select hash, datemodified from object
                  where path = ?''', t)
        data = c.fetchone()
        c.close()
        syncInfo = None
        if data:
            syncInfo = SyncInfo(data[0], data[1])
        return syncInfo

    def removeObjectSyncRecord(self, path):
        conn = self.getConnection()
        c = conn.cursor()
        t = (path, )
        c.execute('delete from object where path = ?', t)
        conn.commit()
        c.close()