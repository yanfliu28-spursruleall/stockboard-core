@echo off
rem ===== 发布钩子示例（默认不启用）=====
rem 启用方法：复制本文件为 publish_hook.cmd（该名字已在 .gitignore 中），按需取消下面某一种方式的注释。
rem 每次刷新成功后 refresh.py 会调用： publish_hook.cmd <dist 目录>
rem dist 里只有 index.html(口令门,约2KB) 和 app.bin(密文)，没有明文业务数据；口令不要放进任何发布产物。
rem
rem 注意：用 git 发布时，请使用一个“只放 dist 内容”的独立仓库，不要在包含无关内容或他人远程的父仓库里 push。

set DIST=%~1

rem --- 方式 A：复制到公司内网共享 / NAS ---
rem robocopy "%DIST%" "\\your-server\share\usboard" /MIR /NFL /NDL /NJH /NJS

rem --- 方式 B：Cloudflare Pages 直传（需先 npm i -g wrangler 并 wrangler login）---
rem wrangler pages deploy "%DIST%" --project-name usboard --commit-dirty=true

rem --- 方式 C：推到一个只放 dist 的独立 git 仓库（把 D:\usboard-site 换成那个仓库的本地路径）---
rem robocopy "%DIST%" "D:\usboard-site" /MIR /XD .git /NFL /NDL /NJH /NJS
rem git -C "D:\usboard-site" add -A
rem git -C "D:\usboard-site" commit -m "refresh" --allow-empty-message
rem git -C "D:\usboard-site" push

exit /b 0
