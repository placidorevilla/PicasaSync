#! /usr/bin/env python

import sys
if sys.hexversion < 0x020700F0:
	raise SystemExit('This scripts needs at least Python 2.7')

import logging, os, mimetypes, calendar, argparse

try:
	import googlecl
except ImportError:
	raise SystemExit('Error importing the googlecl module. In debian/ubuntu you can install it by doing "sudo apt-get install googlecl"')
import googlecl.authentication
import googlecl.config
import googlecl.picasa as picasa
import googlecl.picasa.service as picasa_service

try:
	import iso8601
except ImportError:
	raise SystemExit('Error importing the iso8601 module. In debian/ubuntu you can install it by doing "sudo apt-get install python-iso8601"')

MAX_PHOTOS_PER_ALBUM = 1000

LOG = logging.getLogger("PicasaSync")

supported_types = set(['image/jpeg', 'image/tiff', 'image/x-ms-bmp', 'image/gif', 'image/x-photoshop', 'image/png'])

def get_disk_albums(path, max_photos = None):
	"""Returns a dictionary with all the local albums in the given path

	Args:
		path: directory containing all the local albums
		max_photos: if not None, splice all albums with more than max_photos photos in several albums

	Returns:
		A dictionary of the form:
		{ u'Album title': [(u'Photo title', 'photo_path', last_modification_timestamp), ...] }
	"""
	albums = {}
	for root, dirs, files in os.walk(path):
		supported_files = sorted([f for f in files if mimetypes.guess_type(f)[0] in supported_types])
		if root == path or len(supported_files) == 0:
			continue
		supported_files = [(googlecl.safe_decode(f), os.path.join(root, f), os.stat(os.path.join(root,f)).st_mtime) for f in supported_files]
		album = googlecl.safe_decode(os.path.relpath(root, path))
		if not max_photos or (len(supported_files) < max_photos):
			albums[album] = supported_files
		else:
			for i in xrange(0, (len(supported_files) + max_photos - 1) / max_photos):
				LOG.debug('Splicing album "%s (%s)" with photos from "%s" to "%s"' % (album, i + 1, supported_files[i * max_photos][0], supported_files[min(i * max_photos + max_photos - 1, len(supported_files) - 1)][0]))
				albums[album + ' (%s)' % (i + 1)] = supported_files[i * max_photos:i * max_photos + max_photos]
	return albums

def get_picasa_albums(client):
	"""Returns a dictionary with all the albums in your Picasa account.

	Args:
		client: a googlecl.picasa.service.PhotosServiceCL object

	Returns:
		A dictionary of the form:
		{ u'Album title': gdata.photos.AlbumEntry }
	"""
	picasa_albums = client.build_entry_list(titles = [None], force_photos = False)
	return dict([(googlecl.safe_decode(a.title.text), a) for a in picasa_albums])

def get_picasa_photos(client, album):
	"""Returns a dictionary with all the photos in a given album.

	Args:
		client: a googlecl.picasa.service.PhotosServiceCL object
		album: a gdata.photos.AlbumEntry of the album

	Returns:
		A dictionary of the form:
		{ u'Photo title': gdata.photos.PhotoEntry }
	"""
	picasa_photos = client.GetEntries('/data/feed/api/user/default/albumid/%s?kind=photo' % album.gphoto_id.text)
	return dict([(googlecl.safe_decode(p.title.text), p) for p in picasa_photos])

def get_picasa_client(options = None):
	config = googlecl.config.load_configuration()
	client = picasa_service.SERVICE_CLASS(config)
	client.debug = False if not options else options.debug
	client.email = config.lazy_get(picasa.SECTION_HEADER, 'user')
	auth_manager = googlecl.authentication.AuthenticationManager('picasa', client)
	set_token = auth_manager.set_access_token()
	if not set_token:
		LOG.error('Error using OAuth token. You have to authenticate with googlecl using "google picasa list-albums --force-auth" and following the instructions')
		return None
	return client

def upload_photo(client, album, f, options = None, reason = None):
	if (options and options.dry_run) or LOG.isEnabledFor(logging.INFO):
		msg = 'Uploading file "%s"%s' % (f, (' ' + reason) if reason else '')
		if options and options.dry_run:
			LOG.warn('[DRYRUN] %s' % msg)
		else:
			LOG.info(msg)

	if not options or not options.dry_run:
		client.insert_media_list(album, [googlecl.safe_decode(f)])

def delete_remote_photo(client, photo, options = None, reason = None):
	if reason and ((options and options.dry_run) or LOG.isEnabledFor(logging.INFO)):
		msg = 'Deleting remote photo "%s" %s' % (photo.title.text, reason)
		if options and options.dry_run:
			LOG.warn('[DRYRUN] %s' % msg)
		else:
			LOG.info(msg)

	if not options or not options.dry_run:
		client.Delete(photo)

def replace_remote_photo(client, album, photo, f, options = None, reason = None):
	delete_remote_photo(client, photo, options)
	upload_photo(client, album, f, options, reason)

def create_album(client, title, options = None, reason = None):
	if (options and options.dry_run) or LOG.isEnabledFor(logging.INFO):
		msg = 'Uploading album "%s"%s' % (title, (' ' + reason) if reason else '')
		if options and options.dry_run:
			LOG.warn('[DRYRUN] %s' % msg)
		else:
			LOG.info(msg)

	if not options or not options.dry_run:
		return client.CreateAlbum(title = title, summary = None, access = client.config.lazy_get(picasa.SECTION_HEADER, 'access'), date = None)
	else:
		return None
	pass

def sync(options, path):
	client = get_picasa_client(options)
	if not client:
		return

	disk_albums = get_disk_albums(path, options.max_photos)
	picasa_albums = get_picasa_albums(client)
	
	for album, photos in disk_albums.iteritems():
		if not album in picasa_albums:
			new_album = create_album(client, album, options, 'because it does not exist in Picasa')
			for photo, f, ts in photos:
				upload_photo(client, new_album, f, options)
		else:
			LOG.debug('Checking album "%s"...' % album)
			picasa_photos = get_picasa_photos(client, picasa_albums[album])
			for photo, f, ts in photos:
				if not photo in picasa_photos:
					upload_photo(client, picasa_albums[album], f, options, 'because it is not in the album "%s"' % album)
				elif options.replace and ts > calendar.timegm(iso8601.parse_date(picasa_photos[photo].updated.text).timetuple()):
					replace_remote_photo(client, picasa_albums[album], picasa_photos[photo], f, options, 'because it is newer than the one in the album "%s"' % album)

def run():
	parser = argparse.ArgumentParser(description = 'Sync a directory with your Picasa Web account')
	parser.add_argument('-n', '--dry-run', dest = 'dry_run', action = 'store_true', help = 'Do everything except creating or deleting albums and photos')
	parser.add_argument('-D', '--debug', dest = 'debug', action = 'store_true', help = 'Debug Picasa API usage')
	parser.add_argument('-v', '--verbose', dest = 'verbose', action = 'count', help = 'Verbose output (can be given more than once)')
	parser.add_argument('-m', '--max-photos', metavar = 'NUMBER', dest = 'max_photos', type = int, default = MAX_PHOTOS_PER_ALBUM, help = 'Maximum number of photos in album (limited to %s)' % MAX_PHOTOS_PER_ALBUM)
	parser.add_argument('-u', '--upload', dest = 'upload', action = 'store_true', help = 'Upload missing remote photos')
	parser.add_argument('-d', '--download', dest = 'download', action = 'store_true', help = 'Download missing local photos')
	parser.add_argument('-r', '--replace', dest = 'replace', action = 'store_true', help = 'Replace changed local or remote photos')
	parser.add_argument('--delete-photos', dest = 'delete_photos', action = 'store_true', help = '(DANGEROUS) Delete remote or local photos not present on the other album')
	parser.add_argument('--delete-albums', dest = 'delete_albums', action = 'store_true', help = '(VERY DANGEROUS) Delete remote or local albums not present on the other system')
	parser.add_argument('path', metavar = 'PATH', help = 'Parent directory of the albums to sync')
	options = parser.parse_args()

	if options.verbose == 1:
		log_level = logging.INFO
	elif options.verbose >= 2:
		log_level = logging.DEBUG
	else:
		log_level = logging.WARNING

	logging.basicConfig(level = log_level)

	if options.max_photos > MAX_PHOTOS_PER_ALBUM:
		LOG.warn('Maximum number of photos in album is bigger than the Picasa limit (%s), using this number as limit' % MAX_PHOTOS_PER_ALBUM)
		options.max_photos = MAX_PHOTOS_PER_ALBUM

	if not options.upload and not options.download:
		LOG.info('No upload or download specified. Using bidirectional sync.')
		options.upload = True
		options.download = True

	if (options.delete_photos or options.delete_albums) and options.upload and options.download:
		LOG.warn('You cannot delete when using bidirectional syncing. Disabling deletion.')
		options.delete_photos = False
		options.delete_albums = False

	sync(options, options.path)

def main():
	try:
		run()
	except KeyboardInterrupt:
		pass

if __name__ == '__main__':
		main()

