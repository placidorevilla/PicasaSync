from collections import defaultdict
from functools import wraps
import string, inspect, logging

class dryrun(object):
	class Formatter(string.Formatter):
		def __init__(self, argspec):
			self.argspec = argspec

		def vformat(self, format_string, args, kwargs):
			values = defaultdict(str)
			if self.argspec.defaults:
				values.update(dict(zip(self.argspec.args[-len(self.argspec.defaults):], self.argspec.defaults)))
			values.update(dict(zip(self.argspec.args, args)))
			values.update(kwargs)
			formatted = super(dryrun.Formatter, self).vformat(format_string, args, values)
			if not self.argspec.keywords:
				for k in (k for k in self.used_args if k in kwargs and k not in self.argspec.args):
					del kwargs[k]
			return formatted

		def check_unused_args(self, used_args, args, kwargs):
			self.used_args = used_args

	class descript(object):
		def __init__(self, f, expr, logger, message, prefix, levels):
			self.f = f
			self.expr = expr
			self.logger = logger
			self.prefix = prefix
			self.message = message
			self.levels = levels

		def __get__(self, instance, klass):
			if instance is not None:
				return self.make_bound(instance)

		def __call__(self, *args, **kwargs):
			return self.run(None, args, kwargs)

		def make_bound(self, instance):
			@wraps(self.f)
			def wrapper(*args, **kwargs):
				return self.run(instance, (instance,) + args, kwargs)
			setattr(instance, self.f.__name__, wrapper)
			return wrapper

		def run(self, instance, args, kwargs):
			argspec = inspect.getargspec(self.f)
			local = vars(instance) if instance else self.f.func_dict
			if argspec.defaults:
				local.update(dict(zip(argspec.args[-len(argspec.defaults):], argspec.defaults)))
			local.update(dict(zip(argspec.args, args)))
			local.update(kwargs)
			dry_run = eval(self.expr, self.f.func_globals, local)
			message = dryrun.Formatter(argspec).vformat((self.prefix if dry_run else u'') + self.message, args, kwargs)
			self.logger.log(self.levels[0] if not dry_run else self.levels[1], message)
			if not dry_run:
				return self.f(*args, **kwargs)

	def __init__(self, expr, logger, message, prefix = u'[DRYRUN] ', levels = (logging.INFO, logging.WARN)):
		self.expr = expr
		self.logger = logger
		self.prefix = prefix
		self.message = message
		self.levels = levels

	def __call__(self, f):
		wrapper = dryrun.descript(f, self.expr, self.logger, self.message, self.prefix, self.levels)
		wrapper.__name__ = f.__name__
		wrapper.__doc__ = f.__doc__
		wrapper.__dict__.update(f.__dict__)
		return wrapper

