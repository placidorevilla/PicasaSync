#! /usr/bin/env python

import sys
if sys.hexversion < 0x020700F0:
	raise SystemExit('This scripts needs at least Python 2.7')

import logging, os, mimetypes, argparse, urllib

try:
	import googlecl
except ImportError:
	raise SystemExit('Error importing the googlecl module. In debian/ubuntu you can install it by doing "sudo apt-get install googlecl"')
import googlecl.authentication
import googlecl.config
import googlecl.picasa as picasa
import googlecl.picasa.service as picasa_service

import atom
import gdata.photos
from gdata.photos.service import GooglePhotosException

from dryrun import dryrun

def _entry_ts(entry):
	return int(long(entry.timestamp.text) / 1000)

class InvalidArguments(Exception): pass

class PhotoDiskEntry(object):
	def __init__(self, path, album_path = None):
		self.path = path
		self.timestamp = None
		try:
			if album_path:
				path = os.path.join(album_path, path)
			self.timestamp = int(os.stat(path).st_mtime)
		except:
			pass

class AlbumDiskEntry(object):
	def __init__(self, path):
		self.path = path
		self.timestamp = None
		try:
			self.timestamp = int(os.stat(path).st_mtime)
		except:
			pass

class Photo(object):
	LOG = logging.getLogger('Photo')

	def __init__(self, client, cl_args, album, title = None, disk = None, picasa = None):
		self.client = client
		self.cl_args = cl_args
		self.album = album
		self.disk = disk
		self.picasa = picasa
		if not title:
			if disk:
				self.title = disk.path
			elif picasa:
				self.title = picasa.title.text
			else:
				raise InvalidArguments("No title for photo given and no valid entry found")
		else:
			self.title = title
		self.title = googlecl.safe_decode(self.title)

	@apply
	def path():
		def fget(self):
			return os.path.join(self.album.disk.path, self.disk.path)
		return property(**locals())

	def combine(self, other):
		if self.isInDisk() and not other.isInDisk() and other.isInPicasa():
			self.picasa = other.picasa
		elif self.isInPicasa() and not other.isInPicasa() and other.isInDisk():
			self.disk = other.disk
		else:
			raise InvalidArguments('Tried to combine the photo "%s" with another of the same type' % self.title)

	def isInDisk(self):
		return bool(self.disk) and bool(self.disk.timestamp)

	def isInPicasa(self):
		return bool(self.picasa)

	@dryrun('self.cl_args.dry_run', LOG, 'Uploading file "{self.title}"{reason}')
	def upload(self):
		if self.isInPicasa():
			self.deleteFromPicasa()

		metadata = gdata.photos.PhotoEntry()
		metadata.title = atom.Title(text = self.title)
		metadata.timestamp = gdata.photos.Timestamp(text = str(long(self.disk.timestamp) * 1000))
		try:
			self.picasa = self.client.InsertPhoto(self.album.picasa, metadata, self.path, mimetypes.guess_type(self.path)[0])
		except GooglePhotosException as e:
			self.LOG.error('Error uploading file "{}"'.format(self.title) + e)

	@dryrun('self.cl_args.dry_run', LOG, 'Downloading file "{self.title}"{reason}')
	def download(self):
		timestamp = _entry_ts(self.picasa)
		self.disk = PhotoDiskEntry(self.title, self.album.disk.path)
		tmpfilename = self.path + '.part'
		try:
			urllib.urlretrieve(self.picasa.content.src, tmpfilename)
			os.utime(tmpfilename, (timestamp, timestamp))
			os.rename(tmpfilename, self.path)
		except EnvironmentError as e:
			self.LOG.error('Error downloading file "{}"'.format(self.title) + e)
		else:
			self.disk.timestamp = timestamp

	@dryrun('self.cl_args.dry_run', LOG, 'Deleting file "{self.disk.path}"{reason}')
	def deleteFromDisk(self):
		try:
			os.remove(self.path)
		except EnvironmentError as e:
			self.LOG.error('Cannot delete local file: ' + str(e))
		finally:
			self.disk = None

	@dryrun('self.cl_args.dry_run', LOG, 'Deleting photo "{self.title}"{reason}')
	def deleteFromPicasa(self):
		try:
			self.client.Delete(self.picasa)
		except GooglePhotosException as e:
			self.LOG.error('Error deleting photo "{}"'.format(self.title) + e)
		finally:
			self.picasa = None

	def sync(self):
		if self.isInDisk() and not self.isInPicasa():
			if self.cl_args.upload:
				self.upload(reason = ' because it is not in the album "{0.title}"'.format(self.album))
			if self.cl_args.download and self.cl_args.delete_photos:
				self.deleteFromDisk(reason = ' because it is not in the album "{0.title}"'.format(self.album))
		elif self.isInPicasa() and not self.isInDisk():
			if self.cl_args.upload and self.cl_args.delete_photos:
				self.deleteFromPicasa(reason = ' because it does not exist in the local album')
			if self.cl_args.download:
				self.download(reason = ' because it does not exist in the local album')
		elif self.cl_args.update:
			if self.cl_args.upload and (self.disk.timestamp > _entry_ts(self.picasa) or self.cl_args.force_update):
				self.upload(reason = ' {0}because it is newer than the one in the album "{1.title}"'.format('[FORCED] ' if self.cl_args.force_update else '', self.album))
			if self.cl_args.download and (self.disk.timestamp < _entry_ts(self.picasa) or self.cl_args.force_update):
				self.download(reason = ' {0}because it is newer than the one in the album "{1.title}"'.format('[FORCED] ' if self.cl_args.force_update else '', self.album))

class Album(dict):
	LOG = logging.getLogger('Album')

	def __init__(self, client, cl_args, title = None, disk = None, picasa = None):
		self.client = client
		self.cl_args = cl_args
		self.disk = disk
		self.picasa = picasa
		if not title:
			if disk:
				self.title = disk.path
			elif picasa:
				self.title = picasa.title.text
			else:
				raise InvalidArguments("No title for photo given and no valid entry found")
		else:
			self.title = title
		self.title = googlecl.safe_decode(self.title)
		self.filled_from_disk = False
		self.filled_from_picasa = False

	def combine(self, other):
		if self.isInDisk() and not other.isInDisk() and other.isInPicasa():
			self.picasa = other.picasa
		elif self.isInPicasa() and not other.isInPicasa() and other.isInDisk():
			self.disk = other.disk
		else:
			raise InvalidArguments('Tried to combine the album "%s" with another of the same type' % self.title)

	def fillFromDisk(self, files):
		if self.filled_from_disk:
			return

		for f in files:
			photo = Photo(self.client, self.cl_args, self, disk = PhotoDiskEntry(f, self.disk.path))
			if photo.title in self:
				self[photo.title].combine(photo)
			else:
				self[photo.title] = photo
		self.filled_from_disk = True

	def fillFromPicasa(self):
		if self.filled_from_picasa:
			return

		for photo_entry in self.client.GetEntries('/data/feed/api/user/default/albumid/%s?kind=photo' % self.picasa.gphoto_id.text):
			photo = Photo(self.client, self.cl_args, self, picasa = photo_entry)
			if photo.title in self:
				self[photo.title].combine(photo)
			else:
				self[photo.title] = photo
		self.filled_from_picasa = True

	def isInDisk(self):
		return bool(self.disk) and bool(self.disk.timestamp)

	def isInPicasa(self):
		return bool(self.picasa)

	@dryrun('self.cl_args.dry_run', LOG, 'Creating album "{self.title}"{reason}')
	def upload(self):
		access = googlecl.picasa._map_access_string(self.client.config.lazy_get(picasa.SECTION_HEADER, 'access'))
		try:
			self.picasa = self.client.InsertAlbum(title = self.title, summary = None, access = access, timestamp = str(long(self.disk.timestamp) * 1000))
		except GooglePhotosException as e:
			self.LOG.error('Error creating album "{}"'.format(self.title) + e)
		else:
			for photo in self.itervalues():
				photo.upload()

	@dryrun('self.cl_args.dry_run', LOG, 'Creating directory "{self.title}"{reason}')
	def download(self, root):
		self.disk = AlbumDiskEntry(os.path.join(root, self.title))
		timestamp = _entry_ts(self.picasa)
		try:
			if not os.path.isdir(self.disk.path):
				os.makedirs(self.disk.path)
			os.utime(self.disk.path, (timestamp, timestamp))
		except EnvironmentError as e:
			self.LOG.error('Cannot create local directory: ' + str(e))
		else:
			self.disk.timestamp = timestamp

		self.fillFromPicasa()
		for photo in self.itervalues():
			photo.download()

	@dryrun('self.cl_args.dry_run', LOG, 'Deleting directory "{self.disk.path}"{reason}')
	def deleteFromDisk(self):
		for photo in self.itervalues():
			photo.deleteFromDisk()

		try:
			os.rmdir(self.disk.path)
		except EnvironmentError as e:
			self.LOG.error('Cannot delete local directory: ' + str(e))
		finally:
			self.disk = None

	@dryrun('self.cl_args.dry_run', LOG, 'Deleting album "{self.title}"{reason}')
	def deleteFromPicasa(self):
		try:
			self.client.Delete(self.picasa)
		except GooglePhotosException as e:
			self.LOG.error('Error deleting album "{}"'.format(self.title) + e)
		finally:
			self.picasa = None
	
	def sync(self):
		root = self.cl_args.paths[0]

		if self.isInDisk() and not self.isInPicasa():
			if self.cl_args.upload:
				self.upload(reason = ' because it does not exist in Picasa')
			if self.cl_args.download and self.cl_args.delete_albums:
				self.deleteFromDisk(reason = ' because it does not exist in Picasa')
		elif self.isInPicasa() and not self.isInDisk():
			if self.cl_args.upload and self.cl_args.delete_albums:
				self.deleteFromPicasa(reason = ' because it does not exist locally')
			if self.cl_args.download:
				self.download(root, reason = ' because it does not exist locally')
		else:
			self.LOG.debug('Checking album "%s"...' % self.title)
			self.fillFromPicasa()
			for photo in self.itervalues():
				photo.sync()

class AlbumList(dict):
	LOG = logging.getLogger('AlbumList')

	def __init__(self, client, cl_args):
		self.client = client
		self.cl_args = cl_args
		self.supported_types = set(['image/jpeg', 'image/tiff', 'image/x-ms-bmp', 'image/gif', 'image/x-photoshop', 'image/png'])
		self.filled_from_disk = False
		self.filled_from_picasa = False

	def fillFromDisk(self):
		if self.filled_from_disk:
			return

		for path in self.cl_args.paths:
			for root, dirs, files in os.walk(path):
				supported_files = sorted([f for f in files if mimetypes.guess_type(f)[0] in self.supported_types])
				if len(supported_files) == 0:
					continue
				if root == path:
					album_title = os.path.basename(os.path.normpath(root))
				elif len(self.cl_args.paths) > 1 or googlecl.safe_decode(os.path.basename(os.path.normpath(path))) in self:
					album_title = os.path.join(os.path.basename(os.path.normpath(path)), os.path.relpath(root, path))
				else:
					album_title = os.path.relpath(root, path)
				num_albums = (len(supported_files) + self.cl_args.max_photos - 1) / self.cl_args.max_photos
				full_album_title = album_title
				for i in xrange(0, num_albums):
					if num_albums > 1:
						self.LOG.debug('Splicing album "%s (%s)" with photos from "%s" to "%s"' % (album_title, i + 1, supported_files[i * self.cl_args.max_photos], supported_files[min(i * self.cl_args.max_photos + self.cl_args.max_photos - 1, len(supported_files) - 1)]))
						full_album_title = album_title + ' (%s)' % (i + 1)
					album = Album(self.client, self.cl_args, full_album_title, disk = AlbumDiskEntry(root))
					album.fillFromDisk(supported_files[i * self.cl_args.max_photos:i * self.cl_args.max_photos + self.cl_args.max_photos])
					if album.title in self:
						self[album.title].combine(album)
					else:
						self[album.title] = album
		self.filled_from_disk = True

	def fillFromPicasa(self):
		if self.filled_from_picasa:
			return

		for album_entry in self.client.GetEntries('/data/feed/api/user/default?kind=album'):
			album = Album(self.client, self.cl_args, picasa = album_entry)
			if album.title in self:
				self[album.title].combine(album)
			else:
				self[album.title] = album
		self.filled_from_picasa = True

	def sync(self):
		self.fillFromDisk()
		self.fillFromPicasa()
		for album in self.itervalues():
			album.sync()

class PicasaSync(object):
	MAX_PHOTOS_PER_ALBUM = 1000
	LOG = logging.getLogger('PicasaSync')

	def __init__(self):
		self.parse_cl_args()
		self.get_picasa_client()

	def get_picasa_client(self):
		config = googlecl.config.load_configuration()
		client = picasa_service.SERVICE_CLASS(config)
		client.debug = self.cl_args.debug
		client.email = config.lazy_get(picasa.SECTION_HEADER, 'user')
		auth_manager = googlecl.authentication.AuthenticationManager('picasa', client)
		set_token = auth_manager.set_access_token()
		if not set_token:
			self.LOG.error('Error using OAuth token. You have to authenticate with googlecl using "google picasa list-albums --force-auth" and following the instructions')
			return None
		self.client = client

	def sync(self):
		AlbumList(self.client, self.cl_args).sync()

	def parse_cl_args(self):
		parser = argparse.ArgumentParser(description = 'Sync one or more directories with your Picasa Web account. If only one directory is given and it doesn\'t contain any supported file, it is assumed to be the parent of all the local albums.')
		parser.add_argument('-n', '--dry-run', dest = 'dry_run', action = 'store_true', help = 'Do everything except creating or deleting albums and photos')
		parser.add_argument('-D', '--debug', dest = 'debug', action = 'store_true', help = 'Debug Picasa API usage')
		parser.add_argument('-v', '--verbose', dest = 'verbose', action = 'count', help = 'Verbose output (can be given more than once)')
		parser.add_argument('-m', '--max-photos', metavar = 'NUMBER', dest = 'max_photos', type = int, default = self.MAX_PHOTOS_PER_ALBUM, help = 'Maximum number of photos in album (limited to %s)' % self.MAX_PHOTOS_PER_ALBUM)
		parser.add_argument('-u', '--upload', dest = 'upload', action = 'store_true', help = 'Upload missing remote photos')
		parser.add_argument('-d', '--download', dest = 'download', action = 'store_true', help = 'Download missing local photos')
		parser.add_argument('-r', '--update', dest = 'update', action = 'store_true', help = 'Update changed local or remote photos')
		group = parser.add_argument_group('DANGEROUS', 'Dangerous options that should be used with care')
		group.add_argument('--force-update', dest = 'force_update', action = 'store_true', help = 'Force updating photos regardless of modified status (Assumes --update)')
		group.add_argument('--delete-photos', dest = 'delete_photos', action = 'store_true', help = 'Delete remote or local photos not present on the other album')
		group = parser.add_argument_group('VERY DANGEROUS', 'Very dangerous options that should be used with extreme care')
		group.add_argument('--delete-albums', dest = 'delete_albums', action = 'store_true', help = 'Delete remote or local albums not present on the other system')
		parser.add_argument('paths', metavar = 'PATH', nargs = '+', help = 'Parent directory of the albums to sync')
		cl_args = parser.parse_args()

		if cl_args.verbose == 1:
			log_level = logging.INFO
		elif cl_args.verbose >= 2:
			log_level = logging.DEBUG
		else:
			log_level = logging.WARNING

		logging.basicConfig(level = log_level)

		if cl_args.max_photos > self.MAX_PHOTOS_PER_ALBUM:
			self.LOG.warn('Maximum number of photos in album is bigger than the Picasa limit (%s), using this number as limit' % self.MAX_PHOTOS_PER_ALBUM)
			cl_args.max_photos = self.MAX_PHOTOS_PER_ALBUM

		if not cl_args.upload and not cl_args.download:
			self.LOG.info('No upload or download specified. Using bidirectional sync.')
			cl_args.upload = True
			cl_args.download = True

		if (cl_args.delete_photos or cl_args.delete_albums) and cl_args.upload and cl_args.download:
			self.LOG.warn('You cannot delete when using bidirectional syncing. Disabling deletion.')
			cl_args.delete_photos = False
			cl_args.delete_albums = False

		if cl_args.force_update and cl_args.upload and cl_args.download:
			self.LOG.warn('You cannot force update when using bidirectional syncing. Disabling forced updates.')
			cl_args.force_update = False

		if cl_args.force_update and not cl_args.update:
			cl_args.update = True

		if len(cl_args.paths) > 1 and (cl_args.download or cl_args.delete_albums):
			self.LOG.warn('You cannot download or delete albums when using more than one directories. Disabling download and/or album deletion.')
			cl_args.download = False
			cl_args.delete_albums = False

		self.cl_args = cl_args

def main():
	try:
		PicasaSync().sync()
	except KeyboardInterrupt:
		pass

if __name__ == '__main__':
		main()

