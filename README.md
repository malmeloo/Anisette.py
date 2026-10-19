# Anisette.py

An anisette data provider, but in a Python package! Based on [pyprovision-uc](https://github.com/JayFoxRox/pyprovision-uc).

[Documentation](https://docs.mikealmel.ooo/Anisette.py)

## Usage

Anisette.py has a very simple API.
You, the developer, are responsible for initializing, loading and saving the state of the Anisette engine, either to memory or to disk.
If you want to keep your provisioning session, you **MUST** save and load the state properly. If you don't, you **WILL** run into errors.
See the docs for information on how to do this.

### Initialization

The Anisette provider must be initialized with the macOS ADI library for x86-64.
To save you some effort, a Cloudflare Worker has been set up to provide easy access to these libraries.
By default, the Anisette.py will download this library bundle (~3.5 MB) when initializing a new session,
but you can also provide the library yourself.

Supported executable SHA-256:
`4b6b320f08edcf5d65ed1141399b76a8d9323fee2045c577c5d2f30f35a5dae1`.

```python
from anisette import AnisetteProvider

ani = AnisetteProvider.init(url="https://your-mirror.example/CoreADI.x86_64")

# Use the default mirror:
ani = AnisetteProvider.init(url=None)
```

### Getting Anisette data

The first time you call this method will probably take a few seconds since the virtual device needs to be provisioned first.
After provisioning, getting Anisette data should be quite fast.

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
