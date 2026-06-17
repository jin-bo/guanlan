# 第三方组件声明 / Third-Party Notices

观澜(guanlan-wiki)本体以 **Apache License 2.0** 授权(见 [`LICENSE`](LICENSE))。

为离线、无 CDN、无构建链地运行 Web 宿主(`guanlan-wiki[web]`),本仓库 **vendored**(随 wheel 重分发)
下列第三方前端运行时。各文件的**钉定版本、上游 URL、字节数与 SHA256 校验**记于
[`guanlan/web/static/vendor/README.md`](guanlan/web/static/vendor/README.md)(权威清单);本文件汇总其
**许可**。各组件的完整许可原文以其上游仓库为准。

> The guanlan-wiki project itself is licensed under **Apache License 2.0** (see [`LICENSE`](LICENSE)).
> The following third-party frontend runtimes are **vendored** (redistributed in the wheel) to run the
> Web host offline with no CDN and no build step. Per-file pinned versions, upstream URLs, byte sizes and
> SHA256 checksums are recorded in [`guanlan/web/static/vendor/README.md`](guanlan/web/static/vendor/README.md)
> (the authoritative manifest); this file summarizes their **licenses**. Each component's full license text
> is authoritative at its upstream repository.

| 组件 / Component | 版本 / Version | 用途 / Use | 许可 / License |
|---|---|---|---|
| [mermaid](https://github.com/mermaid-js/mermaid) | 11.15.0 | ` ```mermaid ` 图渲染(P4.13) | MIT |
| [KaTeX](https://github.com/KaTeX/KaTeX)(含 `mhchem`/`auto-render` contrib 与 `KaTeX_*` 字体) | 0.16.22 | 数学/化学渲染(P4.14) | MIT |
| [highlight.js](https://github.com/highlightjs/highlight.js) | 11.10.0(common 构建) | 代码语法高亮(P4.14) | BSD-3-Clause |

---

## MIT License — 适用于 mermaid 与 KaTeX(含其字体)

```
Copyright (c) 2014–2025 Knut Sveidqvist and mermaid contributors        (mermaid)
Copyright (c) 2013–2025 Khan Academy and other contributors             (KaTeX, incl. fonts)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## BSD 3-Clause License — 适用于 highlight.js

```
Copyright (c) 2006, Ivan Sagalaev. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright notice,
      this list of conditions and the following disclaimer in the documentation
      and/or other materials provided with the distribution.
    * Neither the name of the copyright holder nor the names of its contributors
      may be used to endorse or promote products derived from this software
      without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
