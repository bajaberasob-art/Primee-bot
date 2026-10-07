---
name: Python test runtime
description: Replit Python toolchain and package setup needed to run this repository's tests.
---

When the base Nix Python is immutable or lacks `pip`, use Replit's supported Python Tools module and install project dependencies through Replit package management. Do not bypass PEP 668 or force-install into the system interpreter.

**Why:** The base Python 3.13 environment rejected package installation as externally managed; a supported Python 3.11 module allowed the project's declared test dependencies to be used without altering the runtime through a system-level install.

**How to apply:** Check the supported Python modules, select a version satisfying the project's minimum, install packages through the package-management flow, and run tests with that module's interpreter.
