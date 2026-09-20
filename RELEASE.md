# Kenkui server releases

The server's version tracks its own package; the server protocol remains `/v1`.
Private-beta hosted service acceptance is separate from publication of the
source code.

## 10.1.0

Mid-chapter scene breaks get their own pause length. A job may now send
`tts.scenePauseMs`, which reaches Kenkui as `pauses(scene_ms=...)`: the `<hr/>`,
the publisher-labelled separator, or the `* * *` between two scenes inside one
chapter. The tier is off unless a request asks for it, so no queued or stored
job changes behavior, and jobs written before this field decode with zero.

`scenePauses` joins the capability document. A browser must not offer the field
to a server without it: request models ignore fields they do not know, so an
older server would accept `scenePauseMs` and silently render without it.

Requires Kenkui 10.1.1, which is also where a scene ornament stopped being read
aloud. That fix applies to any render on this version, whether or not a job
sets a scene pause: books dividing scenes with a glyph run no longer speak it,
and the segments that held one re-synthesize once. Canonical text is unchanged,
so billing, chapter identity, and offsets are not affected.

Enabling the tier on a book whose paragraph pause is zero moves a segment
boundary and re-renders that book. With a non-zero paragraph pause the boundary
already exists and the change is a pure retune.

## 10.0.0

This release replaces the previous-generation implementation with the pipeline
library, shared local/hosted server, and Kenkui Studio architecture.
The literal package/release version is `10.0.0`; the leading `10` is a nod to
binary two and the second generation.

Previous releases remain available in Git history and tags.
