#!/usr/bin/python2.7
'''
Created on January 18, 2013

@author: Sybrand Strauss
'''

import threading
import time
import logging
import os
import config
import statestore
import sys
import Queue
from bucket.local import LocalBucket
from bucket.abstract import BucketFile


class Sleep(object):
    def __init__(self, sleepTime):
        self._sleepTime = sleepTime

    def perform(self):
        logging.debug('Sleep::perform - begin')
        time.sleep(self._sleepTime)
        logging.debug('Sleep::perform - end')


class Upload(object):
    def __init__(self, objectStore):
        self.objectStore = objectStore
        self.localStore = LocalBucket()
        c = config.Config()
        self.localSyncPath = c.get_home_folder()
        self.state = statestore.StateStore()

    def perform(self):
        logging.debug('Upload::perform - begin')

        if not os.path.exists(self.localSyncPath):
            os.makedirs(self.localSyncPath)
        files = os.listdir(self.localSyncPath)
        for f in files:
            fullPath = os.path.join(self.localSyncPath, f)
            #logging.debug(f)
            if os.path.isdir(fullPath):
                #logging.debug('is directory')
                self.upload_directory(f)
            elif os.path.isfile(fullPath):
                self.processFile(fullPath, f)

        logging.debug('Upload::perform - end')

    def upload_directory(self, remotePath):
        fullPath = os.path.join(self.localSyncPath, remotePath)
        files = os.listdir(fullPath)
        for f in files:
            # we want everything in unicode!
            if isinstance(f, str):
                # strings we encode to iso-8859-1 (on windows)
                f = f.decode('iso-8859-1')
                # then we push it into unicode
                f = unicode(f)
            newLocalPath = os.path.join(fullPath, f)
            newRemotePath = '%s/%s' % (remotePath, f)
            if os.path.isdir(newLocalPath):
                self.upload_directory(newRemotePath)
            elif os.path.isfile(newLocalPath):
                self.processFile(newLocalPath, newRemotePath)

    def processFile(self, localPath, remotePath):
        """ Depending on a number of factors - we do different
        things with files. Maybe we upload the local file,
        maybe we delete it. Maybe we delete the remote file!
        Maybe there's some kind of conflict and we need to rename
        the local file?
        """
        #logging.info('process %s to %s' % (localPath, remotePath))
        remoteFileInfo = self.objectStore.get_file_info(remotePath)
        if remoteFileInfo:
            cmpResult, syncInfo, dm = self.compareFile(localPath,
                                                       remotePath,
                                                       remoteFileInfo)
            if cmpResult:
                # the files are the same!
                # but wait - did we have syncinfo?
                if not syncInfo:
                    # we didn't have sync info!
                    # or it's been invalidated so we
                    # need to store it
                    logging.info('sync info for %s updated' % localPath)
                    self.state.markObjectAsSynced(remotePath,
                                                  remoteFileInfo.hash,
                                                  dm)
            else:
                localFileInfo = self.localStore.get_file_info(localPath)
                syncInfo = self.state.getObjectSyncInfo(remotePath)

                logging.info('remote hash: %r' % remoteFileInfo.hash)
                logging.info('local hash: %r' % localFileInfo.hash)
                logging.info('sync hash: %r' % syncInfo.hash)

                if remoteFileInfo.hash == syncInfo.hash:
                    # the remote file, and our sync record are the same
                    # that means the local version hash changed
                    self.objectStore.upload_object(localPath,
                                                   remotePath,
                                                   localFileInfo.hash)
                    self.state.markObjectAsSynced(remotePath,
                                                  localFileInfo.hash,
                                                  dm)
                elif localFileInfo.hash == syncInfo.hash:
                    # the local file hasn't changed - so it must be the
                    # remote file! the download process should pick this up
                    pass
                else:
                    logging.warn('not implemented!')
                # the files are NOT the same - so either the local
                # one is new, or the remote on is new
                # this is a nasty nasty problem with no perfect solution!
                # lots of thinking to be done here - but in the end
                # it will be some kind of compromise
                # we can however reduce the number of problems:
                # 1) look at the hash we last uploaded
                # 1.1) if the local hash and historic hash are the same
                #      then it means that the remote file is newer, download
                # 1.2) if the historic hash is the same as the remote hash
                #      then we know the local file has changed
                # 1.3) if the historic hash differs from both the remote hash
                #      and the local hash, then we have no way of knowing which
                #      is newer - our only option is to rename the local one
        else:
            # woah - the file isn't online!
            # do we upload the local file? or do we delete it???
            if self.fileHasBeenUploaded(localPath, remotePath):
                # we uploaded this file - but it's NOT online!!
                # this can only mean that it's been deleted online
                # so we need to delete it locally!
                logging.warn('delete local file %s' % localPath)
                os.remove(localPath)
                self.state.removeObjectSyncRecord(remotePath)
            else:
                # the file hasn't been uploaded before, so we upload it now
                self.uploadFile(localPath, remotePath)

    def uploadFile(self, localPath, remotePath):
        logging.warn('upload local file %s' % localPath)
        # before we upload it - we calculate the hash
        localFileInfo = self.localStore.get_file_info(localPath)
        self.objectStore.upload_object(localPath,
                                       remotePath,
                                       localFileInfo.hash)
        localMD = self.localStore.get_last_modified_date(localPath)
        self.state.markObjectAsSynced(remotePath,
                                      localFileInfo.hash,
                                      localMD)

    def fileHasBeenUploaded(self, localPath, remotePath):
        syncInfo = self.state.getObjectSyncInfo(remotePath)
        if syncInfo:
            # we have info for the file - lets check that it's the same info!
            localMD = self.localStore.get_last_modified_date(localPath)
            if syncInfo.dateModified != localMD:
                # the modification date has changed - so it might not be the
                # same file we logged!
                localFileInfo = self.localStore.get_file_info(localPath)
                # if the hash of the local file, is the same as the one we
                # stored then the file hasn't changed since we synced, so
                # we have uploaded this file
                return localFileInfo.hash == syncInfo.hash
            else:
                # the file date hasn't modified, so we assume it hasn't changed
                # if it hasn't changed - it means we've uploaded it
                return True
        else:
            # we don't have any local sync info - so our assumption
            # is that this file has not been uploaded
            return False

    def compareFile(self, localFilePath, remoteFilePath, remoteFileInfo):
        """
        return (True if files are the same, local sync info (if valid/present),
                local last modified date)
        """
        # get sync info for the file
        syncInfo = self.state.getObjectSyncInfo(remoteFilePath)
        localFileInfo = None
        if syncInfo:
            # we have local sync info
            # if the sync modified date, and file modified date are the same
            # then we know for a fact the file is unchanged
            localMD = self.localStore.get_last_modified_date(localFilePath)
            if syncInfo.dateModified != localMD:
                # the dates differ! we need to calculate the hash
                localFileInfo = self.localStore.get_file_info(localFilePath)
                # invalidate the sync info!
                syncInfo = None
            else:
                # the dates are the same, so the hash from the syncInfo
                # should be good
                localFileInfo = BucketFile(remoteFilePath, None, None)
                localFileInfo.hash = syncInfo.hash
        else:
            # we don't have sync info! this means we HAVE to do a hash compare
            localFileInfo = self.localStore.get_file_info(localFilePath)
            localMD = self.localStore.get_last_modified_date(localFilePath)

        return (localFileInfo.hash == remoteFileInfo.hash,
                syncInfo,
                localMD)


class Download(object):
    def __init__(self, objectStore):
        self.objectStore = objectStore
        self.localStore = LocalBucket()
        c = config.Config()
        self.localSyncPath = c.get_home_folder()
        self.tempDownloadFolder = c.get_temporary_folder()
        self.state = statestore.StateStore()

    def perform(self):
        logging.debug('Download::perform - begin')
        # get the current directory
        files = self.objectStore.list_dir(None)
        logging.debug('got %r files' % len(files))
        for f in files:
            if f.isFolder:
                self.download_folder(f)
            else:
                self.download_file(f)
        logging.debug('Download::perform - end')

    def download_file(self, f):
        localPath = self.get_local_path(f.path)
        if not os.path.exists(localPath):
            logging.debug('does not exist: %s' % localPath)
            if self.already_synced_file(f.path):
                # if we've already downloaded this file,
                # it means we have to delete it remotely!
                logging.info('delete remote version of %s' % localPath)
                self.delete_remote_file(f.path)
            else:
                # lets get the file
                tmpFile = self.get_tmp_filename()
                if os.path.exists(tmpFile):
                    os.remove(tmpFile)
                self.objectStore.download_object(f.path, tmpFile)
                os.rename(tmpFile, localPath)
                localMD = self.localStore.get_last_modified_date(localPath)
                self.state.markObjectAsSynced(f.path, f.hash, localMD)
        else:
            # the file already exists - do we overwrite it?
            syncInfo = self.state.getObjectSyncInfo(f.path)
            if syncInfo:
                localMD = self.localStore.get_last_modified_date(localPath)
                if syncInfo.dateModified != localMD:
                    # the dates differ! we need to calculate the hash!
                    localFileInfo = self.localStore.get_file_info(localPath)
                    if localFileInfo.hash != f.hash:
                        # hmm - ok, if the online one, has the same hash
                        # as I synced, then it means the local file
                        # has changed!
                        if syncInfo.hash == f.hash:
                            # online and synced have the same version!
                            # that means the local one has changed
                            # so we're not downloading anything
                            # the upload process should handle this
                            pass
                        else:
                            logging.warn('TODO: the files differ - which '
                                         'one do I use?')
                    else:
                        # all good - the files are the same
                        # we can update our local sync info
                        self.state.markObjectAsSynced(f.path,
                                                      localFileInfo.hash,
                                                      localMD)
                else:
                    # dates are the same, so we can assume the hash
                    # hasn't changed
                    if syncInfo.hash != f.hash:
                        # if the sync info is the same as the local file
                        # then it must mean the remote file has changed!
                        get_file_info = self.localStore.get_file_info
                        localFileInfo = get_file_info(localPath)
                        if localFileInfo.hash == syncInfo.hash:
                            tmpFile = self.get_tmp_filename()
                            if os.path.exists(tmpFile):
                                os.remove(tmpFile)
                            self.objectStore.download_object(f.path, tmpFile)
                            os.remove(localPath)
                            os.rename(tmpFile, localPath)
                            localMD = self.localStore.get_last_modified_date(localPath)
                            self.state.markObjectAsSynced(f.path,
                                                          f.hash,
                                                          localMD)
                        else:
                            logging.info('remote hash: %r' % f.hash)
                            logging.info('local hash: %r' % localFileInfo.hash)
                            logging.info('sync hash: %r' % syncInfo.hash)
                            logging.warn('sync hash differs from local hash!')
                    else:
                        # sync hash is same as remote hash, and the file date
                        # hasn't changed. we assume this to mean, there have
                        # been no changes
                        pass
            else:
                logging.info('TODO: what to do when there is no sync info!')
            pass

    def get_tmp_filename(self):
        return os.path.join(self.tempDownloadFolder, 'tmpfile')

    def download_folder(self, folder):
        # does the folder exist locally?
        logging.debug('download_folder(%s)' % folder.path)
        localPath = self.get_local_path(folder.path)
        downloadFolderContents = True
        if not os.path.exists(localPath):
            # the path exists online, but NOT locally
            # we do one of two things, we either
            # a) delete it remotely
            #     if we know for a fact we've already downloaded this folder,
            #     then it not being here, can only mean we've deleted it
            # b) download it
            #     if we haven't marked this folder as being downloaded,
            #     then we get it now
            if self.already_downloaded_folder(folder.path):
                logging.info('we need to delete %r!' % localPath)
                self.delete_remote_folder(folder.path)
                downloadFolderContents = False
            else:
                logging.info('creating %r..' % localPath)
                os.makedirs(localPath)
                logging.info('done creating %r' % localPath)
        if downloadFolderContents:
            files = self.objectStore.list_dir(folder.path)
            logging.debug('got %r files' % len(files))
            for f in files:
                if folder.path.strip('/') != f.path.strip('/'):
                    if f.isFolder:
                        self.download_folder(f)
                    else:
                        self.download_file(f)

    def get_local_path(self, remote_path):
        return os.path.join(self.localSyncPath, remote_path)

    def already_downloaded_folder(self, path):
        """ Establish if this folder was downloaded before
        typical use: the folder doesn't exist locally, but it
        does exist remotely - that would imply that if we'd already
        downloaded it, it can only be missing if it was deleted, and
        thusly, we delete it remotely.
        """
        logging.warn('TODO: implement check to see '
                     'if %s has already been downloaded' % path)
        return False

    def already_synced_file(self, path):
        """ See: already_downloaded_folder
        """
        syncInfo = self.state.getObjectSyncInfo(path)
        if syncInfo:
            remoteFileInfo = self.objectStore.get_file_info(path)
            if remoteFileInfo.hash == syncInfo.hash:
                # the hash of the file we synced, is the
                # same as the one online.
                # this means, we've already synced this file!
                return True
            return False
        else:
            return False

    def delete_remote_folder(self):
        logging.warn('delete_remote_folder - not implemented')

    def delete_remote_file(self, path):
        self.objectStore.delete_object(path)
        self.state.removeObjectSyncRecord(path)


class Mediator(threading.Thread):
    def __init__(self, objectStore, uiRequestQueue, uiResponseQueue):
        """
        objectStore: probably swift
        uiRequestQueue: requests originating from something
        uiResponsQueue: responses to something
        """
        threading.Thread.__init__(self)
        self.objectStore = objectStore
        self.uiRequestQueue = uiRequestQueue
        self.uiResponseQueue = uiResponseQueue
        self.taskList = list()
        self.running = True
        # retry wait in seconds
        self.retryWait = 1
        self.authenticated = False

    def run(self):
        # the first thing we try to do, is connect

        while self.running:
            if not self.authenticated:
                # not connected? start over! clear all pending tasks
                self.clearPendingTasks()
                # if for some reason we're not connected - connect!
                self.authenticate()
                # if for some reason we failed to connect - sleep
                if not self.authenticated:
                    time.sleep(self.retryWait)
                else:
                    #self.scheduleDownloadTask()
                    self.taskList.put(Upload(self.objectStore))
                    #self.taskList.put(upload)
            else:
                nextTask = self.getNextTask()
                if nextTask:
                    try:
                        nextTask.perform()
                    except ValueError:
                        logging.info('exception processing: %r' %
                                     sys.exc_info()[0])
                    finally:
                        #logging.debug("task complete! time for next task!")
                        if isinstance(nextTask, Upload):
                            #logging.debug('we completed a download')
                            # after downloading - we check for uploads
                            download = Download(self.objectStore)
                            self.taskList.put(download)
                        elif isinstance(nextTask, Download):
                            #logging.debug('we completed a upload')
                            self.taskList.put(Sleep(10))
                            upload = Upload(self.objectStore)
                            self.taskList.put(upload)
                else:
                    time.sleep(0.1)
        logging.info('done running the mediator')

    def authenticate(self):
        logging.debug("going to put authentication...")
        self.uiResponseQueue.put('Authenticating...')
        logging.info('authenticating...')
        if self.objectStore.authenticate():
            self.uiResponseQueue.put('Authenticated')
            self.authenticated = True
            self.retryWait = 1
        else:
            self.authenticated = False
            self.uiResponseQueue.put('Connection failed')
            self.retryWait = self.retryWait * 2

    def clearPendingTasks(self):
        self.taskList = Queue.Queue()

    def getNextTask(self):
        return self.taskList.get()
        #if len(self.taskList) > 0:
        #    return self.taskList.pop(0)
        #return None

    def stop(self):
        self.running = False
