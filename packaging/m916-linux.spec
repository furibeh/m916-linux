Name:           m916-linux
Version:        0.2.0
Release:        1%{?dist}
Summary:        Configuration tool for the Redragon M916-PRO-1K mouse

License:        GPL-3.0-or-later
URL:            https://github.com/furibeh/m916-linux
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
# For %%{_udevrulesdir}
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
# For the GUI tests in %%check
BuildRequires:  python3-pyside6

Requires:       python3-pyside6

%description
Configure the Redragon M916-PRO-1K mouse on Linux without the official Windows
tool: DPI levels and colors, buttons, keyboard shortcuts, macros, polling rate,
sensor and LED options, flash backups and JSON profiles. Includes a command line
tool, a Qt graphical interface and a udev rule that gives the logged-in user
access to the mouse.

%prep
%autosetup -p1

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l m916
install -Dpm 0644 udev/70-redragon-m916.rules %{buildroot}%{_udevrulesdir}/70-redragon-m916.rules
desktop-file-install --dir=%{buildroot}%{_datadir}/applications \
    data/io.github.furibeh.m916-linux.desktop
install -Dpm 0644 m916/icons/io.github.furibeh.m916-linux.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.furibeh.m916-linux.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.furibeh.m916-linux.desktop
QT_QPA_PLATFORM=offscreen %{python3} -m unittest discover -s tests -v

%files -f %{pyproject_files}
%doc README.md docs/
%{_bindir}/m916
%{_bindir}/m916-gui
%{_udevrulesdir}/70-redragon-m916.rules
%{_datadir}/applications/io.github.furibeh.m916-linux.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.furibeh.m916-linux.svg

%changelog
* Fri Oct 30 2026 furibeh <felipeuh846@gmail.com> - 0.2.0-1
- Initial package
