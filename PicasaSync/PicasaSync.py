#! /usr/bin/env python

import sys
if sys.hexversion < 0x020700F0:
	raise SystemExit('This scripts needs at least Python 2.7')

import logging, os, mimetypes, argparse, urllib, multiprocessing, threading, calendar, re, cStringIO, itertools

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

try:
	import pyexiv2
except ImportError:
	raise SystemExit('Error importing the pyexiv2 module. In debian/ubuntu you can install it by doing "sudo apt-get install python-pyexiv2"')

try:
	import dateutil.parser
except ImportError:
	raise SystemExit('Error importing the dateutil module. In debian/ubuntu you can install it by doing "sudo apt-get install python-dateutil"')

try:
	import Image
except ImportError:
	raise SystemExit('Error importing the Image module. In debian/ubuntu you can install it by doing "sudo apt-get install python-imaging"')

# .jpe is not a sane extension for jpeg
mimetypes.init()
if hasattr(mimetypes, '_db') and hasattr(mimetypes._db, 'types_map_inv') and mimetypes._db.types_map_inv[True].has_key('image/jpeg') and '.jpe' in mimetypes._db.types_map_inv[True]['image/jpeg']:
	mimetypes._db.types_map_inv[True]['image/jpeg'].remove('.jpe')

from dryrun import dryrun

def _entry_ts(entry):
	return int(long(entry.timestamp.text) / 1000)

class InvalidArguments(Exception): pass

class PhotoDiskEntry(object):
	def __init__(self, cl_args, path, album_path = None):
		self.path = path
		self.timestamp = None
		if album_path:
			path = os.path.join(album_path, path)
		if 'stat' not in cl_args.origin:
			cl_args.origin.append('stat')
		for origin in cl_args.origin:
			if origin == 'stat':
				try:
					self.timestamp = int(os.stat(path).st_mtime)
					break
				except Exception:
					pass
			elif origin == 'exif':
				metadata = pyexiv2.ImageMetadata(googlecl.safe_decode(path))
				try:
					metadata.read()
					if 'Exif.Image.DateTime' in metadata:
						self.timestamp = calendar.timegm(metadata['Exif.Image.DateTime'].value.timetuple())
						break
				except Exception:
					pass
			else:
				for m in re.finditer(r'\d', self.path):
					try:
						self.timestamp = calendar.timegm(dateutil.parser.parse(m.string[m.start():], fuzzy = True, dayfirst = True).timetuple())
						break
					except ValueError:
						pass
				if self.timestamp:
					break

class AlbumDiskEntry(object):
	def __init__(self, cl_args, path):
		self.path = path
		self.timestamp = None
		if 'stat' not in cl_args.origin:
			cl_args.origin.append('stat')
		for origin in cl_args.origin:
			if origin == 'stat':
				try:
					self.timestamp = int(os.stat(path).st_mtime)
					break
				except Exception:
					pass
			elif origin == 'filename':
				for m in re.finditer(r'\d', self.path):
					try:
						self.timestamp = calendar.timegm(dateutil.parser.parse(m.string[m.start():], fuzzy = True, dayfirst = True).timetuple())
						break
					except ValueError:
						pass
				if self.timestamp:
					break

class Photo(object):
	LOG = logging.getLogger('Photo')
	transforms = {
			1 : (),
			2 : (Image.FLIP_LEFT_RIGHT,),
			3 : (Image.ROTATE_180,),
			4 : (Image.FLIP_TOP_BOTTOM,),
			5 : (Image.ROTATE_90, Image.FLIP_TOP_BOTTOM),
			6 : (Image.ROTATE_270,),
			7 : (Image.ROTATE_90, Image.FLIP_LEFT_RIGHT),
			8 : (Image.ROTATE_90,)
			}

	def __init__(self, album, title = None, disk = None, picasa = None, raw = False):
		self.album = album
		self.disk = disk
		self.picasa = picasa
		self.raw = raw
		if not title:
			if disk:
				self.title = os.path.splitext(disk.path)[0]
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
			raise InvalidArguments(u'Tried to combine the photo "{}" with another of the same type'.format(self.title))

	def isInDisk(self):
		return bool(self.disk) and bool(self.disk.timestamp)

	def isInPicasa(self):
		return bool(self.picasa)

	def isRaw(self):
		return self.raw

	@dryrun('self.album.cl_args.dry_run', LOG, u'Uploading file "{self.disk.path}"{reason}')
	def upload(self):
		if self.isInPicasa():
			if self.album.cl_args.force_update and self.album.cl_args.force_update == 'metadata':
				self.picasa.timestamp = gdata.photos.Timestamp(text = str(long(self.disk.timestamp) * 1000))
				try:
					self.album.client.UpdatePhotoMetadata(self.picasa)
				except GooglePhotosException as e:
					self.LOG.error(u'Error updating metadata for photo "{}": '.format(self.title) + str(e))
				finally:
					return
			else:
				metadata = self.picasa
				metadata.timestamp = gdata.photos.Timestamp(text = str(long(self.disk.timestamp) * 1000))
		else:
			metadata = gdata.photos.PhotoEntry()
			metadata.title = atom.Title(text = self.title)
			metadata.timestamp = gdata.photos.Timestamp(text = str(long(self.disk.timestamp) * 1000))

		mimetype = mimetypes.guess_type(self.path)[0]
		transforms = self.album.cl_args.transform[:] if self.album.cl_args.transform else None
		if transforms:
			original = pyexiv2.ImageMetadata(googlecl.safe_decode(self.path))
			try:
				original.read()
			except Exception as e:
				self.LOG.error(u'Error reading file "{}": '.format(self.disk.path) + str(e))
				return

			if 'raw' in transforms and not self.isRaw():
				transforms.remove('raw')
			if 'resize' in transforms and not (original.dimensions[0] > self.album.cl_args.max_size[0] or original.dimensions[1] > self.album.cl_args.max_size[1]):
				transforms.remove('resize')
			if 'rotate' in transforms and ('Exif.Image.Orientation' not in original or original['Exif.Image.Orientation'].value == 1):
				transforms.remove('rotate')

			if 'raw' in transforms:
				if len(original.previews) == 0:
					self.LOG.error(u'Error getting valid preview from raw file "{}"'.format(self.disk.path))
					return
				try:
					preview = next(x for x in original.previews if (x.dimensions[0] >= self.album.cl_args.max_size[0] or x.dimensions[1] >= self.album.cl_args.max_size[1]) and x.mime_type in AlbumList.standard_types)
				except StopIteration:
					preview = metadata.previews[-1]
				mimetype = preview.mime_type
				if mimetype not in AlbumList.standard_types:
					self.LOG.error(u'Error getting valid preview from raw file "{}"'.format(self.disk.path))
					return
				photo = cStringIO.StringIO(preview.data)
			else:
				photo = cStringIO.StringIO(original.buffer)
#			if 'resize' in transforms or 'rotate' in transforms and mimetype != 'image/jpeg':
			if 'resize' in transforms or 'rotate' in transforms:
				image = Image.open(photo)
				if 'resize' in transforms:
					image.thumbnail(self.album.cl_args.max_size, Image.ANTIALIAS)
				if 'rotate' in transforms:
					for t in self.transforms.get(original['Exif.Image.Orientation'].value, ()):
						image = image.transpose(t)
					original['Exif.Image.Orientation'] = 1
				photo = cStringIO.StringIO()
				# TODO: save in the same format and size approx
				image.save(photo, 'JPEG', quality = 95)
				mimetype = 'image/jpeg'
				photo.seek(0)
#			if 'rotate' in transforms and 'resize' not in transforms and mimetype == 'image/jpeg':
#				# TODO: lossless jpeg rotate
#				pass
			if not self.album.cl_args.strip_exif:
				modified = pyexiv2.ImageMetadata.from_buffer(photo.getvalue())
				modified.read()
				original.copy(modified)
				modified.write()
				photo = cStringIO.StringIO(modified.buffer)
		else:
			if self.album.cl_args.strip_exif:
				original = pyexiv2.ImageMetadata.from_buffer(file(self.path).read())
				original.read()
				for k in original.exif_keys + original.iptc_keys + original.xmp_keys:
					del original[k]
				del original.comment
				original.write()
				photo = cStringIO.StringIO(original.buffer)
			else:
				photo = self.path
		try:
			if self.isInPicasa():
				self.picasa = self.album.client.UpdatePhotoBlob(metadata, photo, mimetype)
			else:
				self.picasa = self.album.client.InsertPhoto(self.album.picasa, metadata, photo, mimetype)
		except GooglePhotosException as e:
			self.LOG.error(u'Error uploading file "{}": '.format(self.disk.path) + str(e))

	@dryrun('self.album.cl_args.dry_run', LOG, u'Downloading photo "{self.title}"{reason}')
	def download(self):
		timestamp = _entry_ts(self.picasa)
		if not self.disk:
			self.disk = PhotoDiskEntry(self.album.cl_args, self.title + mimetypes.guess_extension(self.picasa.content.type), self.album.disk.path)
		if mimetypes.guess_type(self.path)[0] in AlbumList.raw_types:
			self.LOG.warn(u'Not overwriting RAW file "{}"'.format(self.path))
			return
		tmpfilename = self.path + '.part'
		try:
			urllib.urlretrieve(self.picasa.content.src, tmpfilename)
			os.utime(tmpfilename, (timestamp, timestamp))
			os.rename(tmpfilename, self.path)
		except EnvironmentError as e:
			self.LOG.error(u'Error downloading photo "{}": '.format(self.title) + str(e))
		else:
			self.disk.timestamp = timestamp

	@dryrun('self.album.cl_args.dry_run', LOG, u'Deleting file "{self.disk.path}"{reason}')
	def deleteFromDisk(self):
		try:
			os.remove(self.path)
		except EnvironmentError as e:
			self.LOG.error(u'Cannot delete local file: ' + str(e))
		finally:
			self.disk = None

	@dryrun('self.album.cl_args.dry_run', LOG, u'Deleting photo "{self.title}"{reason}')
	def deleteFromPicasa(self):
		try:
			self.album.client.Delete(self.picasa)
		except GooglePhotosException as e:
			self.LOG.error(u'Error deleting photo "{}": '.format(self.title) + str(e))
		finally:
			self.picasa = None

	def sync(self):
		if self.isInDisk() and not self.isInPicasa():
			if self.album.cl_args.upload:
				self.upload(reason = u' because it is not in the album "{0.title}"'.format(self.album))
			if self.album.cl_args.download and self.album.cl_args.delete_photos:
				self.deleteFromDisk(reason = u' because it is not in the album "{0.title}"'.format(self.album))
		elif self.isInPicasa() and not self.isInDisk():
			if self.album.cl_args.upload and self.album.cl_args.delete_photos:
				self.deleteFromPicasa(reason = ' because it does not exist in the local album')
			if self.album.cl_args.download:
				self.download(reason = ' because it does not exist in the local album')
		elif self.album.cl_args.update:
			if self.album.cl_args.upload and (self.disk.timestamp > _entry_ts(self.picasa) or self.album.cl_args.force_update):
				self.upload(reason = u' {0}because it is newer than the one in the album "{1.title}"'.format('[FORCED] ' if self.album.cl_args.force_update else '', self.album))
			if self.album.cl_args.download and (self.disk.timestamp < _entry_ts(self.picasa) or self.album.cl_args.force_update):
				self.download(reason = u' {0}because it is newer than the one in the album "{1.title}"'.format('[FORCED] ' if self.album.cl_args.force_update else '', self.album))

class Album(dict):
	LOG = logging.getLogger('Album')

	def __init__(self, cl_args, title = None, disk = None, picasa = None):
		self.client = None
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
			raise InvalidArguments(u'Tried to combine the album "{}" with another of the same type'.format(self.title))

	def fillFromDisk(self, files):
		if self.filled_from_disk:
			return

		for f in files:
			raw = mimetypes.guess_type(f)[0] in AlbumList.raw_types
			photo = Photo(self, disk = PhotoDiskEntry(self.cl_args, f, self.disk.path), raw = raw)
			if photo.title in self:
				self[photo.title].combine(photo)
			else:
				self[photo.title] = photo
		self.filled_from_disk = True

	def fillFromPicasa(self):
		if self.filled_from_picasa:
			return

		for photo_entry in self.client.GetEntries('/data/feed/api/user/default/albumid/%s?kind=photo' % self.picasa.gphoto_id.text):
			if mimetypes.guess_type(photo_entry.title.text)[0] in AlbumList.standard_types.union(AlbumList.raw_types):
				photo_entry.title = atom.Title(text = os.path.splitext(photo_entry.title.text)[0])
			photo = Photo(self, picasa = photo_entry)
			if photo.title in self:
				self[photo.title].combine(photo)
			else:
				self[photo.title] = photo
		self.filled_from_picasa = True

	def isInDisk(self):
		return bool(self.disk) and bool(self.disk.timestamp)

	def isInPicasa(self):
		return bool(self.picasa)

	@dryrun('self.cl_args.dry_run', LOG, u'Creating album "{self.title}"{reason}')
	def upload(self):
		access = googlecl.picasa._map_access_string(self.client.config.lazy_get(picasa.SECTION_HEADER, 'access'))
		try:
			self.picasa = self.client.InsertAlbum(title = self.title, summary = None, access = access, timestamp = str(long(self.disk.timestamp) * 1000))
		except GooglePhotosException as e:
			self.LOG.error(u'Error creating album "{}": '.format(self.title) + str(e))
		else:
			for photo_title in sorted(self.iterkeys()):
				photo = self[photo_title]
				photo.upload()

	@dryrun('self.cl_args.dry_run', LOG, u'Creating directory "{self.title}"{reason}')
	def download(self, root):
		self.disk = AlbumDiskEntry(self.cl_args, os.path.join(root, self.title))
		timestamp = _entry_ts(self.picasa)
		try:
			if not os.path.isdir(self.disk.path):
				os.makedirs(self.disk.path)
			os.utime(self.disk.path, (timestamp, timestamp))
		except EnvironmentError as e:
			self.LOG.error(u'Cannot create local directory: ' + str(e))
		else:
			self.disk.timestamp = timestamp

		self.fillFromPicasa()
		for photo_title in sorted(self.iterkeys()):
			photo = self[photo_title]
			photo.download()

	@dryrun('self.cl_args.dry_run', LOG, u'Deleting directory "{self.disk.path}"{reason}')
	def deleteFromDisk(self):
		for photo_title in sorted(self.iterkeys()):
			photo = self[photo_title]
			photo.deleteFromDisk()

		try:
			os.rmdir(self.disk.path)
		except EnvironmentError as e:
			self.LOG.error('Cannot delete local directory: ' + str(e))
		finally:
			self.disk = None

	@dryrun('self.cl_args.dry_run', LOG, u'Deleting album "{self.title}"{reason}')
	def deleteFromPicasa(self):
		try:
			self.client.Delete(self.picasa)
		except GooglePhotosException as e:
			self.LOG.error(u'Error deleting album "{}": '.format(self.title) + str(e))
		finally:
			self.picasa = None
	
	def sync(self):
		root = self.cl_args.paths[0]

		if self.isInDisk() and not self.isInPicasa():
			if self.cl_args.upload:
				self.upload(reason = u' because it does not exist in Picasa')
			if self.cl_args.download and self.cl_args.delete_albums:
				self.deleteFromDisk(reason = u' because it does not exist in Picasa')
		elif self.isInPicasa() and not self.isInDisk():
			if self.cl_args.upload and self.cl_args.delete_albums:
				self.deleteFromPicasa(reason = u' because it does not exist locally')
			if self.cl_args.download:
				self.download(root, reason = u' because it does not exist locally')
		else:
			self.LOG.debug(u'Checking album "{}"...'.format(self.title))
			self.fillFromPicasa()
			for photo_title in sorted(self.iterkeys()):
				photo = self[photo_title]
				photo.sync()

class AlbumList(dict):
	LOG = logging.getLogger('AlbumList')
	standard_types = set(['image/jpeg', 'image/x-ms-bmp', 'image/gif', 'image/png'])
	raw_types = set(['image/x-nikon-nef'])

	def __init__(self, clients, cl_args):
		self.clients = clients
		self.cl_args = cl_args
		self.supported_types = self.standard_types
		if self.cl_args.transform and 'raw' in self.cl_args.transform:
			self.supported_types = self.supported_types.union(self.raw_types)
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
						self.LOG.debug(u'Splicing album "{} ({})" with photos from "{}" to "{}"'.format(album_title, i + 1, supported_files[i * self.cl_args.max_photos], supported_files[min(i * self.cl_args.max_photos + self.cl_args.max_photos - 1, len(supported_files) - 1)]))
						full_album_title = album_title + ' (%s)' % (i + 1)
					album = Album(self.cl_args, full_album_title, disk = AlbumDiskEntry(self.cl_args, root))
					album.fillFromDisk(supported_files[i * self.cl_args.max_photos:i * self.cl_args.max_photos + self.cl_args.max_photos])
					if album.title in self:
						self[album.title].combine(album)
					else:
						self[album.title] = album
		self.filled_from_disk = True

	def fillFromPicasa(self):
		if self.filled_from_picasa:
			return

		for album_entry in self.clients[0].GetEntries('/data/feed/api/user/default?kind=album'):
			album = Album(self.cl_args, picasa = album_entry)
			if album.title in self:
				self[album.title].combine(album)
			else:
				self[album.title] = album
		self.filled_from_picasa = True

	def sync(self):
		self.fillFromDisk()
		self.fillFromPicasa()
		if self.cl_args.threads == 1:
			for album_title in sorted(self.iterkeys()):
				album = self[album_title]
				album.client = self.clients[0]
				album.sync()
				del self[album_title]
		else:
			threads = []
			clients = self.clients[:]
			for album_title in sorted(self.iterkeys()):
				album = self[album_title]
				if len(threads) == self.cl_args.threads:
					for i in itertools.cycle(xrange(len(threads))):
						threads[i][0].join(0.1)
						if not threads[i][0].is_alive():
							clients.append(threads[i][1].client)
							del self[album_title]
							del threads[i]
							break
				album.client = clients.pop()
				new_thread = threading.Thread(target = album.sync)
				new_thread.start()
				threads.append((new_thread, album))
			for (thread, album) in threads:
				thread.join()

class ListParser:
	def __init__(self, unique = True, type = str, nargs = None, separator = ',', choices = None):
		self.type = type
		self.separator = separator
		self.choices = choices
		self.unique = unique
		self.nargs = nargs

	def __call__(self, arg):
		if self.nargs:
			arglist = map(self.type, arg.split(self.separator, self.nargs - 1))
			if len(arglist) != self.nargs:
				raise ValueError('Invalid value in list')
		else:
			arglist = map(self.type, arg.split(self.separator))
		if self.unique:
			seen = set()
			arglist = [a for a in arglist if a not in seen and not seen.add(a)]
		if self.choices and any(a not in self.choices for a in arglist):
			raise ValueError('Invalid value in list')
		return arglist

	def __repr__(self):
		return 'list'

class StreamLogger(object):
	def __init__(self, stream, prefix=''):
		self.stream = stream
		self.prefix = prefix
		self.data = ''
		self.encoding = stream.encoding

	def write(self, data):
		self.data += data
		tmp = str(self.data)
		if '\x0a' in tmp or '\x0d' in tmp:
			tmp = tmp.rstrip('\x0a\x0d')
			logging.info('%s%s' % (self.prefix, tmp))
			self.data = ''

class PicasaSync(object):
	MAX_PHOTOS_PER_ALBUM = 1000
	MAX_PHOTO_SIZE = [2048, 2048]
	LOG = logging.getLogger('PicasaSync')

	def __init__(self):
		self.ncores = multiprocessing.cpu_count()
		self.parse_cl_args()
		self.get_picasa_client()
		if len(self.clients) == 0:
			raise Exception('Could not init application')

	def get_picasa_client(self):
		config = googlecl.config.load_configuration()
		self.clients = []
		for i in xrange(self.cl_args.threads):
			client = picasa_service.SERVICE_CLASS(config)
			client.debug = self.cl_args.debug
			client.email = config.lazy_get(picasa.SECTION_HEADER, 'user')
			auth_manager = googlecl.authentication.AuthenticationManager('picasa', client)
			set_token = auth_manager.set_access_token()
			if not set_token:
				self.LOG.error('Error using OAuth token. You have to authenticate with googlecl using "google picasa list-albums --force-auth" and following the instructions')
			self.clients.append(client)

	def sync(self):
		AlbumList(self.clients, self.cl_args).sync()

	def parse_cl_args(self):
		parser = argparse.ArgumentParser(description = 'Sync one or more directories with your Picasa Web account. If only one directory is given and it doesn\'t contain any supported file, it is assumed to be the parent of all the local albums.')
		parser.add_argument('-n', '--dry-run', dest = 'dry_run', action = 'store_true', help = 'Do everything except creating or deleting albums and photos')
		parser.add_argument('-D', '--debug', dest = 'debug', action = 'store_true', help = 'Debug Picasa API usage')
		parser.add_argument('-v', '--verbose', dest = 'verbose', action = 'count', help = 'Verbose output (can be given more than once)')
		parser.add_argument('-m', '--max-photos', metavar = 'NUMBER', dest = 'max_photos', type = int, default = self.MAX_PHOTOS_PER_ALBUM, help = 'Maximum number of photos in album (limited to %s)' % self.MAX_PHOTOS_PER_ALBUM)
		parser.add_argument('-u', '--upload', dest = 'upload', action = 'store_true', help = 'Upload missing remote photos')
		parser.add_argument('-d', '--download', dest = 'download', action = 'store_true', help = 'Download missing local photos')
		parser.add_argument('-r', '--update', dest = 'update', action = 'store_true', help = 'Update changed local or remote photos')
		parser.add_argument('-t', '--threads', dest = 'threads', type = int, nargs = '?', const = self.ncores, default = 1, help = 'Multithreaded operation. Set number of threads to use on album processing. If not given defaults to 1, if given without argument, defaults to number of CPU cores ({} in this system).'.format(self.ncores))
		parser.add_argument('-o', '--origin', dest = 'origin', metavar = 'ORIGINS', type = ListParser(choices = ('filename', 'exif', 'stat')), default = ['exif', 'stat'], help = 'Timestamp origin. ORIGINS is a comma separated list of values "filename", "exif" or "stat" which will be probed in order. Default is "exif,stat".')
		group = parser.add_argument_group('DANGEROUS', 'Dangerous options that should be used with care')
		group.add_argument('--max-size', dest = 'max_size', type = ListParser(unique = False, type = int, nargs = 2), default = self.MAX_PHOTO_SIZE, help = 'Maximum size of photo when using --transform=resize. Default is {},{}.'.format(*self.MAX_PHOTO_SIZE))
		group.add_argument('--force-update', dest = 'force_update', choices = ('full', 'metadata'), nargs = '?', const = 'full', help = 'Force updating photos regardless of modified status (Assumes --update). If no argument given, it assumes full.')
		group.add_argument('--delete-photos', dest = 'delete_photos', action = 'store_true', help = 'Delete remote or local photos not present on the other album')
		group.add_argument('--strip-exif', dest = 'strip_exif', action = 'store_true', help = 'Strip EXIF data from your photos on upload.')
		group.add_argument('--transform', dest = 'transform', metavar = 'TRANSFORMS', type = ListParser(choices = ('raw', 'rotate', 'resize')), help = 'Transform the local files before uploading them. TRANSFORMS is a list of transformations to apply, from "raw", "rotate" and "resize".')
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

		logging.basicConfig(level = log_level, format = '%(asctime)s %(levelname)s [%(thread)x] %(name)s %(message)s')
		sys.stdout = StreamLogger(sys.stdout, '[stdout] ')

		if cl_args.max_photos > self.MAX_PHOTOS_PER_ALBUM:
			self.LOG.warn('Maximum number of photos in album is bigger than the Picasa limit ({}), using this number as limit'.format(self.MAX_PHOTOS_PER_ALBUM))
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

