project('lollypop',
  version: '1.4.48',
  meson_version: '>= 0.46.0',
  default_options: ['cpp_std=c++17']
)

# Dummy C++ source to force C++ compiler detection
dummy_cpp = files('cpp/dummy.cpp')
revision = run_command('bin/revision.sh').stdout().strip()
if revision == ''
  revision=meson.project_version()
endif

if get_option('profile') == 'development'
  appid='org.gnome.Lollypop.Devel'
else
  appid='org.gnome.Lollypop'
endif

i18n = import('i18n')
py_mod = import('python')
project_id = 'org.gnome.Lollypop'

message('Looking for dependencies')
py_installation = py_mod.find_installation('python3')
if not py_installation.found()
    error('No valid python3 binary found')
else
    message('Found python3 binary')
endif

# Dependencies
dependency('gobject-introspection-1.0', version: '>= 1.35.0')
dependency('gtk+-3.0', version: '>= 3.22')
dependency('libsoup-3.0')
dependency('glib-2.0')
dependency('pygobject-3.0', version: '>= 3.29.1')
# SPA host (Apple Music UI). GTK3 path -> WebKit2 4.1, not webkitgtk-6.0.
dependency('webkit2gtk-4.1')

# C++ extension dependencies
gdk_pixbuf_dep = dependency('gdk-pixbuf-2.0', version: '>= 2.36')
gstreamer_dep = dependency('gstreamer-1.0', version: '>= 1.18')
gst_pbutils_dep = dependency('gstreamer-pbutils-1.0', version: '>= 1.18')

# pybind11 - use local headers (header-only library)
pybind11_inc = include_directories('cpp/include')
pybind11_dep = declare_dependency(include_directories: pybind11_inc)

python_dir = join_paths(get_option('prefix'), py_installation.get_install_dir())
LIBEXEC_DIR = join_paths(get_option('prefix'), get_option('libexecdir'))
DATA_DIR = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
bindir = join_paths(get_option('prefix'), get_option('bindir'))

conf = configuration_data()
conf.set('PACKAGE_URL', 'https://wiki.gnome.org/Apps/Lollypop')
conf.set('DATA_DIR', DATA_DIR)
conf.set('LOCALE_DIR', join_paths(get_option('prefix'), get_option('datadir'), 'locale'))
conf.set('PYTHON_DIR', python_dir)
#conf.set('PYTHON_EXEC_DIR', join_paths(get_option('prefix'), py_installation.get_path('stdlib')))
conf.set('libexecdir', LIBEXEC_DIR)
conf.set('VERSION', meson.project_version())
conf.set('REVISION', revision)
conf.set('APPID', appid)

subdir('data')
subproject('po')
subdir('search-provider')

install_subdir(
  'lollypop',
  install_dir: python_dir
)

install_subdir(
  'backend',
  install_dir: python_dir
)

# C++ Extension Module
# ======================
# Build lollypop_ext C++ module and install alongside Python modules

# C++ compiler flags
cpp_args = ['-std=c++17', '-O3', '-fvisibility=hidden', '-Wall', '-Wextra',
            '-DGDK_PIXBUF_DISABLE_DEPRECATED', '-DGST_DISABLE_DEPRECATED']
if host_machine.system() == 'linux'
    cpp_args += ['-fPIC']
endif

# Sources
ext_sources = [
    'cpp/module.cpp',
    'cpp/color_extractor.cpp',
    'cpp/blur.cpp',
]

# Build shared library with Python extension suffix using custom_target with build script
# On Linux: .cpython-3XX-x86_64-linux-gnu.so
# Use CPython's real EXT_SUFFIX so the installed module is importable
# (e.g. lollypop_ext.cpython-314-x86_64-linux-gnu.so, not ...-linux.so).
ext_suffix = run_command(
    py_installation,
    ['-c', 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))'],
    check: true,
).stdout().strip()
ext_name = 'lollypop_ext' + ext_suffix

lollypop_ext = custom_target(
    'lollypop_ext',
    output: ext_name,
    input: ext_sources,
    command: [
        meson.current_source_dir() + '/cpp/build_ext.sh',
        '@OUTPUT@',
        '@INPUT@',
    ],
    install: true,
    install_dir: join_paths(python_dir, 'lollypop'),
)

#install_subdir(
#  'thirdparty',
#  install_dir: join_paths(python_dir, 'lollypop')
#)

message('Preparing init file')
configure_file(
  input: 'lollypop.in',
  output: 'lollypop',
  configuration: conf,
  install_dir: bindir
)

meson.add_install_script('meson_post_install.py')
