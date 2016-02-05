# kvmbackup
live backup of kvm virtual guests with external snapshot and blockcommit

### Syntax
usage: kvm_backup.py [-h] [-d DEST] [-k KEEP] [-t TIMEOUT] [-n] [-D DISKS]
                     vms [vms ...]

positional arguments:
  vms                   virtual machines to backup

optional arguments:
  -h, --help            show this help message and exit
  -d DEST, --dest DEST  Backup destination folder
  -k KEEP, --keep KEEP  Number of backups to keep
  -t TIMEOUT, --timeout TIMEOUT
                        Number of minutes to wait for blockcommit to finish
  -n, --dryrun          do not perform backup just inform
  -D DISKS, --disks DISKS
                        backup all disks if this list of disks is empty. This
                        option can be used multiple times
