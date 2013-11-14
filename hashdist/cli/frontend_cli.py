from __future__ import print_function

import os
import sys
import shutil
from pprint import pprint
import subprocess

from .main import register_subcommand
from ..core import BuildStore

def add_build_args(ap):
    ap.add_argument('-j', metavar='CPUCOUNT', default=1, type=int, help='number of CPU cores to utilize')
    ap.add_argument('-k', metavar='KEEP_BUILD', default="never", type=str,
            help='keep build directory: always, never, error (default: never)')

def add_profile_args(ap):
    ap.add_argument('-p', '--profile', default='default.yaml', help='yaml file describing profile to build (default: default.yaml)')

def add_develop_args(ap):
    ap.add_argument('-l', '--link', default='absolute', help='Link action: one of [absolute, relative, copy] (default: absolute)')

def add_target_args(ap):
    ap.add_argument('target', nargs='?', default='', help='directory to use for build dir (default: profile/package name)')
    ap.add_argument('-f', '--force', action='store_true', help='overwrite output directory')

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

    def build_profile_deps(self):
        ready = self.builder.get_ready_list()
        if len(ready) == 0:
            sys.stdout.write('[Profile dependencies are up to date]\n')
        else:
            while len(ready) != 0:
                self.builder.build(ready[0], self.ctx.get_config(),
                        self.args.j, self.args.k)
                ready = self.builder.get_ready_list()
            sys.stdout.write('[Profile dependency build successful]\n')

    def ensure_target(self, target):
        if os.path.exists(target):
            if self.args.force:
                shutil.rmtree(target)
            else:
                self.ctx.error("%s already exists (use -f to overwrite)" % target)
        os.mkdir(target)

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
        from ..core import atomic_symlink

        if not self.args.profile.endswith('.yaml'):
            self.ctx.error('profile filename must end with yaml')

        profile_symlink = self.args.profile[:-len('.yaml')]
        if self.args.package is not None:
            self.builder.build(self.args.package, self.ctx.get_config(), self.args.j, self.args.k)
        else:
            self.build_profile_deps()
            artifact_id, artifact_dir = self.builder.build_profile(self.ctx.get_config())
            atomic_symlink(artifact_dir, profile_symlink)
            sys.stdout.write('Profile build successful, link at: %s\n' % profile_symlink)


@register_subcommand
class Develop(ProfileFrontendBase):
    """
    Builds a development profile in the Hashdist YAML profile spec
    format, at the same location as the profile yaml file, but without
    the .yaml suffix.

    Note that Develop uses absolute symlinks by default, but supports
    relative symlinks and copying as well.
    """
    command = 'develop'

    @classmethod
    def setup(cls, ap):
        add_profile_args(ap)
        add_build_args(ap)
        add_target_args(ap)
        add_develop_args(ap)

    def profile_builder_action(self):
        if not self.args.profile.endswith('.yaml'):
            self.ctx.error('profile filename must end with yaml')
        develop_profile = self.args.profile[:-len('.yaml')]
        if self.args.target:
            target = os.path.abspath(self.args.target)
        else:
            target = os.path.abspath(develop_profile)

        self.ensure_target(target)
        self.build_profile_deps()
        self.builder.build_profile_out(target, self.ctx.get_config(), self.args.link)
        sys.stdout.write('Development profile build %s successful\n' % target)


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
        ap.add_argument('package', help='package to show information about')
        add_target_args(ap)

    def profile_builder_action(self):
        ensure_target(self.args.target)
        build_spec = self.builder.get_build_spec(self.args.package)
        self.build_store.prepare_build_dir(self.source_cache, build_spec, self.args.target)

def system_lib(name):
    if name == "":
        return True
    system_libs = [
            # linux
            "linux-vdso",
            "linux-gate",

            # libc
            "libc",
            "libm",
            "libutil",
            "libcrypt",
            "libpthread",
            "libdl",
            "librt",
            "libnsl",

            # gcc
            "libstdc++",
            "libgfortran",
            "libquadmath",
            "libgcc_s",

            # X11
            "libX11",
            "libXau",
            "libXext",
            "libxcb",
            "libXdmcp",
            ]

    for lib in system_libs:
        if name.startswith(lib + ".so"):
            return True
    return False

def check_lib(filename, artifact_path):
    s = subprocess.check_output(["ldd", filename])
    lines = s.split("\n")
    # Fill the libs_dict with library names, paths and addresses
    libs_dict = {}
    for line in lines:
        line = line.strip()
        if line == "":
            continue
        if "=>" in line:
            lib, rest = line.split("=>")
        else:
            lib = ""
            rest = line
        rest = rest.strip()
        idx = rest.rfind(" ")
        if idx == -1:
            path = ""
            address = rest
        else:
            path = rest[:idx]
            address = rest[idx:]

        lib = lib.strip()
        path = path.strip()
        address = address.strip()
        libs_dict[lib] = (path, address)

    for lib in libs_dict:
        if system_lib(lib):
            continue
        path, address = libs_dict[lib]
        if path.startswith(artifact_path):
            # Our lib
            continue
        print("Lib:", filename)
        print(lib, path, address)
        print()

def check_libs(profile, artifact_path):
    s = subprocess.check_output(["find", "-L", profile + "/", "-name", "*.so*"])
    libs = s.split()
    for lib in libs:
        check_lib(lib, artifact_path)

@register_subcommand
class CheckLibs(object):
    """
    Check that .so libraries are linked against our own libraries

    Example::

        $ hit check-libs default

    """

    command = 'check-libs'

    @staticmethod
    def setup(ap):
        ap.add_argument('profile', help='profile to check')

    @staticmethod
    def run(ctx, args):
        profile_path = args.profile
        build_store = BuildStore.create_from_config(ctx.get_config(), ctx.logger)
        print("Checking libs in '%s'..." % profile_path)
        check_libs(profile_path, build_store.artifact_root)
