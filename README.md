# Anisette.py

An anisette data provider, but in a Python package! Based on [pyprovision-uc](https://github.com/JayFoxRox/pyprovision-uc).

Still experimental, and stability of the underlying VM remains to be seen.

## Prerequisites

Libraries from the Apple Music Android app are required to set up the provider at least once.
The bundle generated from this after initialization can be used for all your future provisioning needs.

The required APK file can be downloaded [here](https://web.archive.org/web/20231226115856/https://apps.mzstatic.com/content/android-apple-music-apk/applemusic.apk).
The download of this file will eventually be integrated into Anisette.py itself.

## Usage

Anisette.py has a very simple API.
You, the developer, are responsible for initializing, loading and saving the state of the Anisette engine, either to memory or to disk.
If you want to keep your provisioning session, you **MUST** save and load the state properly. If you don't, you **WILL** run into errors.

### Initialization

```python
from anisette import Anisette

ani = Anisette.init("applemusic.apk")

# Alternatively, you can init from a file-like object:
with open("applemusic.apk", "rb") as f:
    ani = Anisette.init(f)
```

### Saving state

State is saved to two separate bundles: a "provisioning" bundle and a "library" bundle. The provisioning bundle
is specific to your provisioning session and can **NOT** be shared across sessions. The library bundle **CAN** be
shared across sessions, but this is not required. It is also possible to save both the provisioning state and libraries
to a single bundle. This may be easier to work with, but requires more disk space (3-4 megabytes per saved session).

```python
ani.save("bundle.bin")

# Alternatively, to save both bundles separately:
ani.save("provisioning.bin", "libs.bin")

# You can also use file objects:
with open("provisioning.bin", "wb+") as pf, open("libs.bin", "wb+") as lf:
    ani.save(pf, lf)
```

### Loading state

As mentioned before, if you want to keep your provisioning session across restarts, you must load it properly.

```python
ani = Anisette.load("bundle.bin")

# Alternatively, to load both bundles separately:
ani = Anisette.load("provisioning.bin", "libs.bin")

# Once again, you can also use file objects:
with open("provisioning.bin", "rb") as pf, open("libs.bin", "rb") as lf:
    ani = Anisette.load(pf, lf)
```

### Getting Anisette data

```python
ani.get_data()

# Returns:
# {
#   'X-Apple-I-MD': '...',
#   'X-Apple-I-MD-M': '...',
#   ...
# }
```

## Credits

A huuuge portion of the work has been done by [@JayFoxRox](https://github.com/JayFoxRox/)
in their [pyprovision-uc](https://github.com/JayFoxRox/pyprovision-uc) project.
I just rewrote most of their code :-)
