import os
import sys
import shutil
from pprint import pprint
from .main import register_subcommand

def add_build_args(ap):
    ap.add_argument('-j', metavar='CPUCOUNT', default=1, type=int, help='number of CPU cores to utilize')
    ap.add_argument('-k', metavar='KEEP_BUILD', default="never", type=str,
            help='keep build directory: always, never, error (default: never)')
    ap.add_argument('-c', "--copy", help='Create a copy of the profile')

def add_profile_args(ap):
    ap.add_argument('-p', '--profile', default='default.yaml', help='yaml file describing profile to build (default: default.yaml)')

class ProfileFrontendBase(object):
    def __init__(self, ctx, args):
        from ..spec import Profile, ProfileBuilder, load_profile, TemporarySourceCheckouts
        from ..core import BuildStore, SourceCache
        self.ctx = ctx
        self.args = args
        self.source_cache = SourceCache.create_from_config(ctx.get_config(), ctx.logger)
        self.build_store = BuildStore.create_from_config(ctx.get_config(), ctx.logger)
        self.checkouts = TemporarySourceCheckouts(self.source_cache)
        self.profile = load_profile(self.checkouts, args.profile)
        self.builder = ProfileBuilder(self.ctx.logger, self.source_cache, self.build_store, self.profile)

    @classmethod
    def run(cls, ctx, args):
        self = cls(ctx, args)
        try:
            self.profile_builder_action()
        finally:
            self.checkouts.close()


@register_subcommand
class Build(ProfileFrontendBase):
    """
    Builds a profile in the Hashdist YAML profile spec format, and
    outputs a symlink to the resulting profile at the same location
    as the profile yaml file, but without the .yaml suffix.

    If you provide the package argument to build a single package, the
    profile symlink will NOT be updated.
    """
    command = 'build'

    @classmethod
    def setup(cls, ap):
        add_profile_args(ap)
        add_build_args(ap)
        ap.add_argument('package', nargs='?', help='package to build (default: build all)')

    def profile_builder_action(self):
        from ..core import atomic_symlink, make_profile
        from ..core.run_job import unpack_virtuals_envvar

        if not self.args.profile.endswith('.yaml'):
            self.ctx.error('profile filename must end with yaml')

        profile_symlink = self.args.profile[:-len('.yaml')]
        if self.args.package is not None:
            self.builder.build(self.args.package, self.ctx.get_config(), self.args.j)
        else:
            ready = self.builder.get_ready_list()
            if len(ready) == 0:
                sys.stdout.write('Up to date, link at: %s\n' % profile_symlink)
            else:
                while len(ready) != 0:
                    self.builder.build(ready[0], self.ctx.get_config(),
                            self.args.j, self.args.k)
                    ready = self.builder.get_ready_list()
                sys.stdout.write('Profile build successful, link at: %s\n' % profile_symlink)
            artifact_id, artifact_dir = self.builder.build_profile(self.ctx.get_config())
            atomic_symlink(artifact_dir, profile_symlink)

            if self.args.copy:
                sys.stdout.write('Creating a copy at: %s\n' % self.args.copy)
                virtuals = unpack_virtuals_envvar(os.environ.get('HDIST_VIRTUALS', ''))
                make_profile(logger, ctx.build_store, [{"id": profile_aid}],
                        args.copy, virtuals, hdist_config)


@register_subcommand
class Status(ProfileFrontendBase):
    """
    Status a profile in the Hashdist YAML profile spec format, and
    outputs a symlink to the resulting profile at the same location
    without the .yaml suffix.
    """
    command = 'status'

    @classmethod
    def setup(cls, ap):
        add_profile_args(ap)

    def profile_builder_action(self):
        report = self.builder.get_status_report()
        report = sorted(report.values())
        for build_spec, is_built in report:
            status = 'OK' if is_built else 'needs build'
            sys.stdout.write('%-50s [%s]\n' % (build_spec.short_artifact_id, status))

@register_subcommand
class Show(ProfileFrontendBase):
    """
    Shows (debug) information for building a profile
    """
    command = 'show'

    @classmethod
    def setup(cls, ap):
        add_profile_args(ap)
        ap.add_argument('subcommand', choices=['buildspec', 'script'])
        ap.add_argument('package', help='package to show information about')

    def profile_builder_action(self):
        if self.args.subcommand == 'buildspec':
            if self.args.package == 'profile':
                spec = self.builder.get_profile_build_spec()
            else:
                spec = self.builder.get_build_spec(self.args.package)
            pprint(spec.doc)
        elif self.args.subcommand == 'script':
            sys.stdout.write(self.builder.get_build_script(self.args.package))
        else:
            raise AssertionError()

@register_subcommand
class BuildDir(ProfileFrontendBase):
    """
    Creates the build directory, ready for build, in a given location, for debugging purposes
    """
    command = 'bdir'

    @classmethod
    def setup(cls, ap):
        add_profile_args(ap)
        ap.add_argument('-f', '--force', action='store_true', help='overwrite output directory')
        ap.add_argument('package', help='package to show information about')
        ap.add_argument('target', help='directory to use for build dir')

    def profile_builder_action(self):
        if os.path.exists(self.args.target):
            if self.args.force:
                shutil.rmtree(self.args.target)
            else:
                self.ctx.error("%d already exists (use -f to overwrite)")
        os.mkdir(self.args.target)
        build_spec = self.builder.get_build_spec(self.args.package)
        self.build_store.prepare_build_dir(self.source_cache, build_spec, self.args.target)

