#! /usr/bin/env python3

# The MIT License (MIT)
#
# Copyright (c) 2016 leif
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

__author__ = 'leif'

import libvirt
import sys
from xml.etree import ElementTree
import time
import shutil
import subprocess
import logging
import os.path
import datetime
import argparse


class FatalKvmBackupException(Exception):
    pass

# logging.basicConfig(filename='example.log',level=logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)

# logging.debug('This message should go to the log file')
# logging.info('So should this')
# logging.warning('And this, too')
BACKUP_DST = '/tmp'
BACKUP_SPACE_MARGIN = 10*1024**3
BACKUP_FREE_SPACE = 0
date_format = "%Y-%m-%dT%H%M%S"
args = None
conn = None  # connection to hypervisor

import smtplib


class Sender(object):
    """E-mail stuff to people"""
    def __init__(self):
        self.subject = ''
        self.content = ''
        self.fromaddr = "KVM backup <no_reply@example.com>"
        self.toaddrs = ["leif@example.com", ]
        self.mailserver = "mailserver.example.com"

    def mail_it(self):
        # Add the From: and To: headers at the start!
        server = smtplib.SMTP(self.mailserver)
        content_to_send = self.content
        msg = "From: %s\r\nSubject: %s\r\nTo: %s\r\n\r\n%s" % (self.fromaddr, self.subject, ", ".join(self.toaddrs),
                                                               content_to_send)
        # server.set_debuglevel(1)
        server.sendmail(self.fromaddr, self.toaddrs, msg)
        server.quit()


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


class Device(object):
    def __init__(self, file, device, allocation):
        self.file = file
        self.file_base = os.path.basename(file)
        self.file_dir = os.path.dirname(file)
        self.dev = device
        self.allocation = allocation


class Dom(object):
    def __init__(self, dom):
        self.dom = dom
        self.persistent_xml = ''
        self.devices = []
        self.devices_not_snapshotted = []
        self.TOTAL_ALLOCATED_SIZE = 0
        self.__get_target_devices()

    # Function to return a list of block devices used.
    def __get_target_devices(self):
        global BACKUP_FREE_SPACE
        global BACKUP_SPACE_MARGIN
        global args

        self.devices = []
        self.TOTAL_ALLOCATED_SIZE = 0
        self.__update_persistent_xml()
        if len(self.devices) == 0:
            # Create a XML tree from the domain XML description.
            tree = ElementTree.fromstring(self.dom.XMLDesc(0))

            for target in tree.findall("devices/disk"):
                if target.get('device') == 'disk' and target.get('type') == 'file':
                    try:
                        dev_name = target.find('target').get('dev')
                        dev_file = target.find('source').get('file')
                        if None not in (dev_name, dev_file):
                            if (args.disks is None) or (dev_name in args.disks):
                                lst = self.dom.blockInfo(dev_name)
                                if len(lst) == 3:
                                    # allocation host storage in bytes occupied by the image
                                    dev_allocation = lst[1]
                                    self.TOTAL_ALLOCATED_SIZE += dev_allocation
                                    logging.debug("Found: {:s} {:s} allocation:{:s} TOTAL:{:s}".format(
                                        dev_file, dev_name, sizeof_fmt(dev_allocation),
                                        sizeof_fmt(self.TOTAL_ALLOCATED_SIZE)))
                                    if (self.TOTAL_ALLOCATED_SIZE + BACKUP_SPACE_MARGIN) > BACKUP_FREE_SPACE:
                                        send_error("backup directory free space too small " +
                                                   sizeof_fmt(BACKUP_FREE_SPACE))
                                        print("backup directory free space too small " +
                                              sizeof_fmt(BACKUP_FREE_SPACE))
                                        sys.exit(1)
                                    self.devices.append(Device(dev_file, dev_name, dev_allocation))
                            else:
                                # add drives we do not want to snapshot
                                self.devices_not_snapshotted.append(Device(dev_file, dev_name, 0))

                    except AttributeError as err:
                        print("did not expect AttributeError:" + str(err))
                    except Exception as err:
                        # this is not a disk we can copy
                        print(str(err))
                        pass

    def __update_persistent_xml(self):
        self.persistent_xml = self.dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE | libvirt.VIR_DOMAIN_XML_INACTIVE)

    def __get_existing_backups(self):
        backups = []
        global BACKUP_DST
        global args
        backup_dst_mine = os.path.join(BACKUP_DST, self.dom.name())
        try:
            if not args.dryrun:
                os.makedirs(backup_dst_mine, exist_ok=True)
            dir_content = os.listdir(backup_dst_mine)
            dir_content.sort(reverse=True)
            for i, item in enumerate(dir_content):
                try:
                    t1 = datetime.datetime.strptime(item, date_format)
                    logging.debug("{:d} {:s}".format(i, t1.strftime(date_format)))
                    backups.append(t1)
                except Exception as err:
                    logging.debug(str(err))
            logging.debug("number of backups:{:d} keep is:{:d}".format(len(backups), args.keep))
        except OSError as err:
            send_error("backup destination unavailable {:s}".format(str(err)))
            raise FatalKvmBackupException(err)
        return backups

    def get_current_file(self, dev_name):
        # dom_tmp = conn.lookupByName(self.dom.name())
        tree = ElementTree.fromstring(self.dom.XMLDesc(0))

        for target in tree.findall("devices/disk"):
            if target.get('device') == 'disk' and target.get('type') == 'file':
                try:
                    dev_name_tmp = target.find('target').get('dev')
                    dev_file_tmp = target.find('source').get('file')
                    if dev_name_tmp == dev_name:
                        return dev_file_tmp
                except Exception:
                    # this is not a disk we can copy
                    pass
        raise FatalKvmBackupException('get_current_file() cannot find device name ' + dev_name)

    def create_external_snapshot(self, backup_time):
        global date_format
        global args
        snap = None

        # SNAPSHOT XML example
        # -----------------------
        # https://libvirt.org/formatsnapshot.html
        #
        # snapshotXML = "<domainsnapshot>" \
        #               "<name>{:s}</name>" \
        #               "<description>external snapshot for backup</description>" \
        #               "<memory snapshot='no'></memory>" \
        #               "<disks>" \
        #               "  <disk name='{:s}' snapshot='external'>" \
        #               "     <source file='{:s}'></source>" \
        #               "     <driver type='qcow2'></driver>" \
        #               "  </disk>" \
        #               "</disks>" \
        #               "<state>disk-snapshot</state>" \
        #               "</domainsnapshot>".format(backup_time.strftime(date_format), device.dev,
        #                                          tmp_snapshot_filename)
        #

        # create snapshot xml via ElementTree
        root = ElementTree.Element('domainsnapshot')
        name = ElementTree.SubElement(root, 'name')
        name.text = backup_time.strftime(date_format)
        tmp1 = ElementTree.SubElement(root, 'description')
        tmp1.text = 'external snapshot for backup'
        tmp1 = ElementTree.SubElement(root, 'memory')
        tmp1.set('snapshot', 'no')
        disks = ElementTree.SubElement(root, 'disks')

        for device in self.devices:
            tmp_snapshot_filename = "{:s}/{:s}_{:s}".format(device.file_dir,
                                                            backup_time.strftime(date_format),
                                                            device.file_base)
            disk = ElementTree.SubElement(disks, 'disk')
            disk.set('name', device.dev)
            disk.set('snapshot', 'external')
            source = ElementTree.SubElement(disk, 'source')
            source.set('file', tmp_snapshot_filename)
            driver = ElementTree.SubElement(disk, 'driver')
            driver.set('type', 'qcow2')

        for not_used_dev in self.devices_not_snapshotted:
            disk = ElementTree.SubElement(disks, 'disk')
            disk.set('name', not_used_dev.dev)
            disk.set('snapshot', 'no')

        tmp1 = ElementTree.SubElement(root, 'state')
        tmp1.text = 'disk-snapshot'
        snapshot_xml = ElementTree.tostring(root, encoding='unicode')

        if args.dryrun:
            print("Will create snapshot with this XML:")
            print(snapshot_xml)
            # raise libvirt.libvirtError('test')
        else:
            snap = self.dom.snapshotCreateXML(
                snapshot_xml,
                flags=libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE |
                libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_NO_METADATA |
                libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY |
                libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC)
        return snap

    def blockcommit(self, device, backup_time):
        global args
        global date_format

        disk = device.dev
        base = None  # will be the bottom of the chain
        top = None  # the active image at the top of the chain will be used
        self.dom.blockCommit(disk, base, top, flags=libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE)
        # libvirt.VIR_DOMAIN_BLOCK_COMMIT_DELETE # not possible with leaving job running
        timeout_time = datetime.datetime.now() + datetime.timedelta(minutes=args.timeout)
        while True:
            try:
                ret = self.dom.blockJobAbort(
                    disk,
                    flags=libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_ASYNC |
                    libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
                if ret == 0:
                    # remove the temporary image when blockcommit is done
                    tmp_snapshot_filename = "{:s}/{:s}_{:s}".format(
                        device.file_dir, backup_time.strftime(date_format), device.file_base)

                    timeout_file_remove = datetime.datetime.now() + datetime.timedelta(seconds=20)
                    while datetime.datetime.now() < timeout_file_remove:
                        logging.debug("blockcommit: done remove tmp file:" + tmp_snapshot_filename + " current is:" +
                                      self.get_current_file(device.dev))
                        if self.get_current_file(device.dev) != tmp_snapshot_filename:
                            try:
                                os.remove(tmp_snapshot_filename)
                                break
                            except OSError:
                                send_error("Cannot remove temporary snapshot file " + tmp_snapshot_filename)
                                break
                        time.sleep(1)

                    if self.get_current_file(device.dev) == tmp_snapshot_filename:
                        send_error("Timeout removing temporary snapshot file " + tmp_snapshot_filename)
                    break
                if datetime.datetime.now() > timeout_time:
                    raise FatalKvmBackupException("Timeout in blockcommit for {:s} {:s} (minutes {:d})".format(
                        self.dom.name(), device.dev, args.timeout
                    ))
                time.sleep(1)
            except libvirt.libvirtError as err:
                logging.debug("blockcommit: waiting for pivot " + str(err))
                time.sleep(1)

    def begin_backup(self):
        global BACKUP_DST
        global args
        global date_format

        # used to check that the destionation is available before starting backup
        self.__get_existing_backups()

        backup_dst_mine = os.path.join(BACKUP_DST, self.dom.name())  # this destination must exist now !
        if os.path.exists(backup_dst_mine):
            # directory exists and we already know there is space in the main BACKUP_DST from dom loading
            backup_time = datetime.datetime.now()
            backup_dir = os.path.join(backup_dst_mine, backup_time.strftime(date_format))
            backup_completed_successfully = False
            try:
                backup_xml_file = os.path.join(backup_dir, "{:s}.xml".format(self.dom.name()))
                if args.dryrun:
                    print("** will create " + backup_dir)
                    print("** save xml to " + backup_xml_file)
                    device = None
                    try:
                        print("** starting snapshot(s) for " + self.dom.name() + " " +
                              ' '.join(map(lambda x: x.file_base, self.devices)))
                        self.create_external_snapshot(backup_time)
                        for device in self.devices:
                                # check that we are running on new file
                                current_file = self.get_current_file(device.dev)
                                print("** run cp --sparse=always " + device.file + " " + backup_dir)
                                if current_file != device.file:
                                    # copy the original file
                                    subprocess.check_call("cp --sparse=always " + device.file +
                                                          " " + backup_dir, shell=True)
                                print("** doing self.blockcommit(device)")
                    except libvirt.libvirtError as err:
                        # cleanup the device copy process
                        # directory cleanup below in backup_completed_successfully
                        if device is not None:
                            # the backup was started need to check for snapshots in devices
                            for device in self.devices:
                                current_file = self.get_current_file(device.dev)
                                logging.debug("current file:{:s} original file:{:s}".format(current_file, device.file))
                                if current_file != device.file:
                                    self.blockcommit(device, backup_time)
                        raise FatalKvmBackupException(err)

                    self.cleanup_backup()
                    backup_completed_successfully = True
                else:
                    os.mkdir(backup_dir)
                    # copy xml
                    f = open(backup_xml_file, 'w')
                    f.write(self.persistent_xml)
                    f.close()
                    device = None
                    try:
                        logging.debug("starting snapshot(s) for " + self.dom.name() + " " +
                                      ' '.join(map(lambda x: x.file_base, self.devices)))
                        snap = self.create_external_snapshot(backup_time)
                        if snap is None:
                            # this code should not be entered since an exception would be thrown in libvirt
                            logging.debug("This code should not be entered"
                                          " Snapshot creation failed " + self.dom.name())
                        else:
                            libvirt_errors = []
                            for device in self.devices:
                                # check that we are running on new file
                                current_file = self.get_current_file(device.dev)
                                logging.debug("** run cp --sparse=always " + device.file + " " + backup_dir)
                                if current_file != device.file:
                                    subprocess.check_call("cp --sparse=always " +
                                                          device.file + " " + backup_dir, shell=True)

                                logging.debug("** doing self.blockcommit(device) device=" + device.dev)
                                try:
                                    self.blockcommit(device, backup_time)
                                except libvirt.libvirtError as err:
                                    libvirt_errors.append(err)
                            if libvirt_errors:
                                raise FatalKvmBackupException(
                                    "Cannot blockcommit after snapshot for " + self.dom.name())
                    except libvirt.libvirtError as err:
                        # cleanup the device copy process
                        # directory cleanup below in backup_completed_successfully
                        if device is not None:
                            # the backup was started need to check for snapshots in devices
                            for device in self.devices:
                                current_file = self.get_current_file(device.dev)
                                logging.debug("current file:{:s} original file:{:s}".format(current_file, device.file))
                                if current_file != device.file:
                                    self.blockcommit(device, backup_time)
                        raise FatalKvmBackupException(err)

                    self.cleanup_backup()
                    backup_completed_successfully = True
            except OSError as err:
                send_error("cannot create backup directory {:s}".format(str(err)))
                raise FatalKvmBackupException(err)
            finally:
                if not backup_completed_successfully:
                    send_error("backup failed for {:s} in backup directory:{:s}".format(self.dom.name(), backup_dir),
                               subject="Backup failed for {:s} {:s}".format(
                                   self.dom.name(), backup_time.strftime(date_format)))
                    logging.debug("backup failed!")
                    # os.rmdir(backup_dir)
                else:
                    send_error("backup completed for {:s} in backup directory:{:s}".format(self.dom.name(), backup_dir),
                               subject="Backup completed for {:s} {:s}".format(self.dom.name(),
                                                                               backup_time.strftime(date_format)))

        else:
            send_error("backup destination unavailable ({:s})".format(backup_dst_mine))

    def cleanup_backup(self):
        global args
        global BACKUP_DST
        global date_format
        backup_dst_mine = os.path.join(BACKUP_DST, self.dom.name())  # this destination must exist now !
        if os.path.exists(backup_dst_mine):
            # directory exists and we already know there is space in the main BACKUP_DST from dom loading
            all_backups = self.__get_existing_backups()
            if len(all_backups) > args.keep:
                backups_to_remove = all_backups[args.keep:]
                for item in reversed(backups_to_remove):
                    backup_dir = os.path.join(backup_dst_mine, item.strftime(date_format))
                    if args.dryrun:
                        print("** will remove:" + backup_dir)
                    else:
                        try:
                            shutil.rmtree(backup_dir)
                        except (PermissionError, FileNotFoundError):
                            send_error("Cannot remove backup folder " + backup_dir)


def send_error(msg, subject=None):
    if subject is None:
        subject = 'KVM backup' + ' '.join(args.vms)
    sender = Sender()
    sender.subject = subject
    sender.content = msg
    sender.mail_it()


def parse_arguments(myargs):
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dest", type=str, default='/tmp', help="Backup destination folder")
    parser.add_argument("-k", "--keep", type=int, default=2, help="Number of backups to keep")
    parser.add_argument("-t", "--timeout", type=int, default=60,
                        help="Number of minutes to wait for blockcommit to finish")
    parser.add_argument("-n", "--dryrun",  action="store_true", help='do not perform backup just inform')
    parser.add_argument("-D", "--disks",  action='append', help='backup all disks if this list of disks is empty. '
                                                                'This option can be used multiple times')
    parser.add_argument('vms', metavar='vms', nargs='+', help='virtual machines to backup')
    return parser.parse_args(myargs)

if __name__ == "__main__":
    args = parse_arguments(sys.argv[1:])
    BACKUP_DST = args.dest
    try:
        tmp = shutil.disk_usage(BACKUP_DST)
        BACKUP_FREE_SPACE = tmp.free
    except FileNotFoundError:
        send_error("Backup destination insufficient resources: {:s}".format(BACKUP_DST))
        sys.exit(1)

    uri = "qemu:///system"
    conn = libvirt.open(uri)
    if conn is None:
        send_error("Failed to open connection to the hypervisor " + uri)
        sys.exit(1)

    hypervisor_name = conn.getHostname()
    # logging.debug("The follwing machines are running on: " + hypervisor_name)
    # active domains
    # domains = conn.listDomainsID()
    try:
        for vm in args.vms:
            dom_tmp = conn.lookupByName(vm)
            if dom_tmp.isActive() == 1:
                domain = Dom(dom_tmp)
                domain.begin_backup()
            else:
                print(vm + " is not active, cannot make active backup")
                send_error("vm is not active, cannot make active backup", subject="Backup failed for {:s} ".format(vm))

    except Exception as e:
        print(str(e))
        send_error(str(e))
        sys.exit(1)
    conn.close()
