# Third-party notices

Mota's original application code is proprietary. The following third-party components are
used under their own licenses; see `third_party_manifest.json` for versions.

- **PySide6 / shiboken6** — LGPL-3.0 / GPL / commercial. Mota uses dynamically linked Qt
  Widgets, QtSvg and QOpenGLWidget. Distributors must comply with the applicable Qt license
  and LGPL relinking requirements. Qt Quick 3D is not used.
- **pydantic** (MIT), **typer** (MIT), **rich** (MIT), **PyYAML** (MIT),
  **platformdirs** (MIT), **keyring** (MIT), **argon2-cffi** (MIT),
  **python-docx** (MIT), **pytest** (MIT) — permissive licenses.
- **openai** (Apache-2.0) — optional model client.
- **pyqtgraph** (MIT), **PyOpenGL** (BSD-3-Clause), **numpy** (BSD-3-Clause),
  **pypdf** (BSD-3-Clause), **sexpdata** (BSD-3-Clause).
- **KiCad libraries** — CC-BY-SA 4.0 with the design-output exception when obtained; not
  redistributed in this repository. The demo catalog is Mota-authored engineering data.
- **PyDracula** and **OpenBoardView** — MIT; used as visual/interaction references only.
  No upstream source code is copied into this repository.

Full license texts for installed packages are available in each package's `dist-info`
directory in the active environment. This file is a notice summary, not legal advice.
