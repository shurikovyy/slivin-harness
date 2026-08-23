# Windows setup и проверенные ограничения

## 1. Проверенная среда

Разработка Harness велась на корпоративной Windows-машине без обязательного administrator access.

Проверенная конфигурация:

```text
Windows 10 build 19045
Git Bash / MINGW64
Codex CLI 0.148.0
Python 3.11-compatible runtime
portable Node
project-local Python venv
```

Исторически на первой машине Codex/Node/project venv были user-local installations,
но их конкретные filesystem paths **не являются Harness contract**.

Текущая конфигурация:

```text
Harness bootstrap Python → environment/PATH
Codex                  → local config/environment/PATH
project repository     → [projects.<name>].repo
project Python/Node/Jest → [projects.<name>.toolchain]
```

Harness не требует изменения global `PATH`: absolute user-local paths можно хранить в ignored `harness.local.toml`.

---

## 2. Почему используем Codex App Server

App Server запускается из установленного Codex CLI:

```text
codex app-server
```

Это не отдельный продукт/install.

Harness вызывает executable напрямую.

Это удобно в корпоративной среде:

- global PATH менять не обязательно;
- admin install не нужен;
- версия Codex контролируется user-local installation.

---

## 3. Самая сложная проблема Windows sandbox: split writable roots

Первоначально `workspace-write` через App Server/Codex exec создавал writable roots:

```text
[workdir, /tmp, $TMPDIR]
```

Unelevated Windows restricted-token sandbox не мог безопасно enforce несколько разнесённых writable roots и fail-closed сообщал примерно:

```text
windows unelevated restricted-token sandbox
cannot enforce split writable root sets directly;
refusing to run unsandboxed
```

Попытка использовать elevated sandbox была неприемлема: она требовала administrator credentials.

### Рабочее решение

App Server запускается со strict config:

```text
sandbox_workspace_write.exclude_slash_tmp=true
sandbox_workspace_write.exclude_tmpdir_env_var=true
```

В результате workspace-write boundary становится:

```text
[workdir]
```

Это позволило без admin:

- читать/писать project workspace;
- применять patch;
- запускать shell;
- запускать Python;
- запускать pytest;
- запускать Node/Jest.

### Важно

Это проверенный workaround для конкретной версии Codex CLI, а не вечная гарантия.

После обновления Codex CLI нужно повторно проверить sandbox probes.

---

## 4. Task-local temp

После удаления внешних temp writable roots тесты и инструменты должны писать temp внутрь workspace:

```text
<workspace>/.harness_tmp/
```

Harness задаёт child-process environment примерно так:

```text
TEMP=.harness_tmp
TMP=.harness_tmp
TMPDIR=.harness_tmp
XDG_CACHE_HOME=.harness_tmp/cache
NPM_CONFIG_CACHE=.harness_tmp/npm
PYTHONDONTWRITEBYTECODE=1
```

Плюсы:

- sandbox не требует write за пределами workspace;
- Jest/Python cache не загрязняет product diff;
- runtime artifacts принадлежат конкретной задаче.

---

## 5. `.harness_tmp` должна быть ignored

Outer Harness repo игнорирует task workspaces целиком.

Для static historical inner repo `.harness_tmp/` добавляется через:

```text
.git/info/exclude
```

Для managed linked Git worktree используется worktree-specific:

```text
.harness_git_excludes
core.excludesFile=<workspace>/.harness_git_excludes
```

Это позволяет не менять project `.gitignore`.

---

## 6. PowerShell Constrained Language

Windows sandbox может запускать PowerShell в Constrained Language Mode.

На практике были блокированы некоторые:

```text
[System.IO.File] static calls
reflection/dynamic .NET operations
```

При этом нормально работали:

```text
Get-Content
Get-ChildItem
git
Python
Node
pytest/Jest
```

Правило:

> Для нетривиальной обработки файлов предпочитать маленький Python script вместо попыток обходить PowerShell restrictions.

---

## 7. Git Bash / MSYS argument conversion

Git Bash автоматически преобразует некоторые Unix-looking аргументы для native Windows executables.

Например при прямом вызове:

```text
cmd.exe /d /c ...
```

может понадобиться:

```bash
MSYS2_ARG_CONV_EXCL='*'
```

Это особенно важно для low-level sandbox probes.

Не добавлять этот env глобально без необходимости.

---

## 8. App Server process health

Длинный structured Planner/Evaluator turn может несколько минут не выдавать user-facing текст.

Harness поэтому показывает heartbeat:

```text
[PLANNING] working...
elapsed=04:20
app-server=alive
last-event=00:06
```

Adapter отдельно проверяет:

```text
process.poll()
last protocol activity
turn timeout
```

Если App Server умер, Controller должен обнаружить это раньше общего 15-минутного ожидания.

---

## 9. Structured output и `final_answer`

App Server может emit:

```text
commentary agentMessage
final_answer agentMessage
```

Первая реализация клиента склеивала все completed agent messages:

```text
JSON1
JSON2
```

что приводило к:

```text
JSONDecodeError: Extra data
```

Исправление:

```text
phase=final_answer → authoritative
fallback → last completed agent message
```

Этот transport detail является частью архитектуры, а не косметикой.

---

## 10. Streaming implementer output

`item/agentMessage/delta` — chunk одного сообщения, а не отдельное сообщение.

Поэтому:

```text
newline after every delta
```

сломал бы читаемость.

Текущая схема:

```text
stream delta без newline
item/completed(agentMessage)
→ separator/newline
```

---

## 11. EOL и file mode — разные проблемы

Диагностика:

```bash
git ls-files --eol py run
git diff --ignore-space-at-eol -- py run
git diff --summary -- py run
```

Можно иметь:

```text
i/lf w/lf
```

и всё равно видеть modification из-за:

```text
100755 → 100644
```

На Windows filesystem executable bit ведёт себя иначе, чем на Linux.

Рекомендуемый local setting:

```bash
git config core.filemode false
```

При этом в repository index shell launchers `run` и `py` должны оставаться:

```text
100755
```

чтобы Linux/macOS clone получил executable files.

---

## 12. `.gitattributes`

Harness фиксирует EOL policy:

```text
*.py   LF
*.toml LF
*.md   LF
*.cjs  LF
run    LF
py     LF
*.cmd  CRLF
```

Если новая `.gitattributes` расходится с уже committed blobs, может потребоваться отдельный:

```bash
git add --renormalize ...
```

commit.

Не путать EOL normalization с file-mode change.

---

## 13. Проверка новой Windows-машины

После clone:

```bash
./py tools/self_check.py
```

Создать local config:

```bash
cp harness.local.example.toml harness.local.toml
# заполнить Codex/project/toolchain paths этой машины
```

Проверить Codex тем executable, который указан в config, либо через PATH:

```bash
codex --version
codex login status
```

Если Codex не в PATH — вызвать configured absolute path напрямую.

Перед использованием новой версии Codex рекомендуется повторить:

1. App Server initialize smoke.
2. Read-only turn.
3. Workspace-write sandbox probe.
4. Python test/temp probe.
5. Structured Planner/Evaluator smoke.

---

## 14. Известный редкий MSYS failure

В одном historical planning run Git Bash subprocess выдал:

```text
fatal error - CreateFileMapping ... Win32 error 5
```

App Server при этом оставался жив, heartbeat продолжался и turn завершился.

Пока это считается transient Windows/MSYS subprocess failure, а не архитектурным blocker.

Если станет повторяемым — предпочтительное направление: использовать native `git.exe`/native process boundary вместо дополнительного bash subprocess.


---

## 15. Project `core.autocrlf` и project-specific EOL gate

В `sa_icover` historical workspaces наблюдалось:

```text
core.autocrlf=true
```

Поэтому Git index и physical worktree могут выглядеть:

```text
i/lf
w/crlf
attr/text=auto
```

Это допустимо само по себе.

Для project changes authoritative EOL policy лучше проверять существующим project checker:

```text
tools/check_changed_eol.py
```

а не делать вывод только из raw worktree bytes.

Отдельно помнить про UTF-8 BOM: line-ending checker и BOM preservation — не одно и то же.

---

## 16. Codex CLI upgrade contract

Tested App Server version:

```text
0.148.0
```

Protocol/schema и Windows sandbox behavior считаются version-sensitive.

После upgrade:

1. сгенерировать/прочитать новые App Server protocol schemas;
2. проверить initialize;
3. read-only turn;
4. workspace-write probe;
5. temp/Python probe;
6. structured output;
7. skill discovery;
8. process death/heartbeat behavior.

Не переносить старые protocol assumptions на новую версию без smoke.


---

## 17. Managed Git worktree на Windows

Обычные project tasks теперь создают linked Git worktree автоматически.

Git metadata остаётся в source repository, а task directory содержит `.git` file.

Чтобы иметь worktree-local ignore policy, Harness один раз включает:

```text
extensions.worktreeConfig=true
```

и задаёт:

```text
core.excludesFile=<task-worktree>/.harness_git_excludes
```

Это проверяется `tools/self_check.py` на temporary Git repository без зависимости от `sa_icover`.

Перед новым release/tag всё равно нужен smoke на реальной Windows-машине, потому что файловые locks, path syntax и Codex sandbox остаются platform-sensitive.
