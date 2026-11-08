# heal workflow — `guanlan heal`

This file is the heal workflow of the `guanlan-wiki` skill. For terms and hard rules, read [`../SKILL.md`](../SKILL.md). For page format, read [`conventions.md`](conventions.md).

The heal workflow makes content pages for high-frequency missing entities. A high-frequency missing entity is a broken link. Two or more pages link to it, but it has no page (`lint.missing_entity`).

**Write all page content in Chinese.** This file is in English. The wiki is in Chinese. Page text, titles, index lines, and log lines must be in Chinese.

## Terms

Each term below has one meaning in this file.

- **Target**: one missing entity name in this batch. The wrapper gives all targets in the prompt.
- **Linking page**: an existing page that links to a target. The wrapper gives the list of linking pages for each target.
- **Source page**: a page in `wiki/sources/`. One source page records one source document.
- **Citation**: `[[source page]]:line`. A citation points to one line of a source page.

## Steps

Do these steps for each target, in this order.

1. **Read the linking pages.** Read only the linking pages of the target and `wiki/index.md`. Do not read the full wiki. Do not search for other linking pages. The target name is already a normalized key. Use it as the file name.
2. **Choose the directory `<dir>`.**
   - An entity (a person, an organization, a model, a system) goes in `entities/`.
   - A concept (a method, a theory, a term, an algorithm, an architecture, a training technique) goes in `concepts/`.
   - If you are not sure, treat the target as an entity.
   - `SCHEMA.md` controls only the split between entities and concepts. It cannot add a third directory.
3. **Choose A, B, or C.** Use the decision tree below when the linking pages show that the target needs a page. Make every new page in the `<dir>/` from step 2.

   To find out if a page exists, use the `glob` tool (for example `wiki/*/产业链*.md`) or the `list_directory` tool. Do not use shell commands.

   ```
   Is the target a variant of an existing page in entities/ or concepts/? (index.md or a linking page shows this)
     Yes → C: Add the target name to the end of the aliases of that page. Set last_updated to today.
              Do not make a new page. Do not change the body. Do not change other frontmatter.
     No  → Is the target name standard enough to use as a file name?
              Yes → A: Make <dir>/<target>.md. A is the default. [[target]] then resolves to the new page.
              No (the target is informal or an abbreviation, and a linking page gives the full name)
                  → B: Make <dir>/<full name>.md. Write the target name in frontmatter aliases (for example aliases: ['大模型']).
                       If you do not write the alias, the link stays broken and the receipt records still_broken.
   Not sure → choose A.
   ```

4. **Write only facts that the linking pages contain.** Do not write a fact that no linking page contains. If a linking page has `sources` in its frontmatter, you can copy them to the new page. If no linking page has them, leave `sources` empty. An empty `sources` is valid.
5. **Copy each direct quote and its citation.** You cannot read the source documents. You can read only the linking pages. Thus every direct quote in the new page must come from a linking page.
   - Copy the words of the quote exactly. Do not change a character. Do not shorten the quote. Do not join two quotes into one. Keep each "……" that the linking page has.
   - Copy the citation that the linking page gives for the quote. The citation has the form `[[source page]]:line`.
   - Do not cite the linking page instead of the source page. These forms are wrong: `[[某概念页]]:行` and `[[某概念页]]:行，原注 :行`.
   - If the linking page paraphrases a statement, paraphrase it in the new page too. Do not put quotation marks around a paraphrase.

   Example. Line 12 of the linking page `concepts/X.md` is:

   > 讲话指出"把调查研究作为基本功"（[[某讲话]]:88）。

   Correct: `讲话指出"把调查研究作为基本功"（[[某讲话]]:88）。`

   Wrong: `讲话指出"把调查研究作为基本功"（[[X]]:12）。`

   For the other quote rules, read conventions §直接引语.
6. **Add the page to the index.** Add one line to the section of `wiki/index.md` that matches `<dir>`.
   - For a new page, write: `- [<title>](<dir>/<stem>.md) — <one sentence>`.
   - For B and C, put the alias at the end of the line, for example `…（别名：大模型/LLM）`. The duplicate check, the 2-gram search, and `health` use this line to find the alias.
7. **Write the log.** Add one line to `wiki/log.md`: `## [YYYY-MM-DD] heal | <target>`.
8. **Skip a target that you cannot write.** Skip the target in these conditions:
   - The linking pages do not give enough content.
   - The target is better as a topic page.
   - You cannot decide.

   Give the reason in one sentence. The reason has no required format. The wrapper counts the broken links again to find the result. It does not read your reason.

## Write scope

The heal workflow makes only these writes:

- Make a new page in `entities/` or `concepts/`.
- Add an alias to the `aliases` of an existing page.
- Edit `index.md`.
- Add lines to `log.md`.

Do not delete the body of an existing page. Do not write the body again. Do not change `raw/`. The write audit of the wrapper marks these writes as `unexpected_write`:

- A new page outside `entities/` and `concepts/`.
- A change to the body of an existing page.
- A change to a frontmatter field of an existing page, other than `aliases` and `last_updated`.
- A deleted page.
- A symbolic link that replaces a page.
- An alias that is not a target of this batch.

For the end of the workflow, read SKILL.md〈写工作流收尾（共用）〉.
