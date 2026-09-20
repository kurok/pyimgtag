# Interop verification: Lightroom Classic and digiKam

pyimgtag writes hierarchical keywords for the two applications most people
graduate into. Every part of that is verified automatically except the part
that matters most to a user: whether those applications actually show the tree.

This page is the checklist for closing that gap. It takes about ten minutes and
needs one thing this project cannot supply — a copy of Lightroom Classic or
digiKam.

## What is already verified, and what is not

CI writes the tags with exiftool and reads them back with exiftool
(`tests/test_hierarchy.py::TestExiftoolRoundTrip`). That proves the values land
in `XMP-lr:HierarchicalSubject` and `XMP-digiKam:TagsList` intact, with the flat
`XMP-dc:Subject` still beside them.

It proves nothing about *interpretation*. A file can carry a perfectly formed
`HierarchicalSubject` and still import as seven flat keywords, or as a tree with
the separator showing, or with the accented city mangled. Only the applications
themselves can settle that, and neither of them is scriptable, so this last
step is done by hand and recorded here.

## Generate the fixture

```bash
python -m pyimgtag.interop_fixture --out-dir ~/Desktop/pyimgtag-interop
```

Three files:

| File | What it checks |
|---|---|
| `pyimgtag-interop-embedded.jpg` | the tree embedded in the image itself |
| `pyimgtag-interop-sidecar.jpg` | deliberately carries **no** metadata |
| `pyimgtag-interop-sidecar.xmp` | the same tree in a companion sidecar |

Both paths need checking. A DAM can read embedded XMP happily and ignore
sidecars, or the reverse, and RAW workflows live entirely in sidecars — so a
tree that only ever reached embedded metadata would miss exactly the people who
most want one.

The fixture is a synthetic image and needs no photo library, no model and no
network. To use your own photos instead:

```bash
pyimgtag run --input-dir ~/Pictures/some-photos --write-exif \
    --hierarchical-keywords --write-rating
pyimgtag faces apply --write-exif --hierarchical-keywords
pyimgtag events apply --write-keywords --hierarchical-keywords
```

## The tree to expect

One fixture file exercises every branch the taxonomy can emit: two people, a
three-level place with a non-ASCII city, a plain tag, a controlled-vocabulary
tag, an event and a star rating.

<!-- expected-tree: kept in sync with pyimgtag.interop_fixture.expected_paths()
     by tests/test_interop_fixture.py — edit the sample there, not here. -->

```
Events|Portugal 2026
People|Alice Marques
People|Bo Nilsson
Places|Portugal|Leiria|Óbidos
Tags|Nature|Coast|beach
Tags|castle
Tags|sunset
```

Alongside the tree, both files carry:

- the flat keywords `Alice Marques, Bo Nilsson, Portugal 2026, beach, castle, sunset`
- a rating of **4 of 5 stars**
- the description `pyimgtag interop fixture: sunset over the castle at Obidos`

The description spells Óbidos without its accent on purpose: it is written into
IPTC as well as XMP, and pyimgtag does not declare `IPTC:CodedCharacterSet`, so
non-ASCII text there would arrive with an undeclared encoding and could display
mangled for reasons unrelated to the keyword tree. Everything this fixture puts
into IPTC is ASCII; the accented `Óbidos` under `Places` travels in XMP only,
which is where it belongs and where it should survive intact.

## digiKam

1. **Settings → Configure digiKam → Metadata**, and turn on *Read from sidecar
   files*. Without it the sidecar half of this check silently passes by doing
   nothing.
2. Add the fixture directory as an album (**Album → Open in File Manager** is
   not enough — use **Settings → Configure digiKam → Collections**).
3. Select both images, then **Item → Reread Metadata from File**.
4. Open the **Tags** panel on the left.

Expect `Events`, `People`, `Places` and `Tags` as four roots, nesting as listed
above — `Portugal` inside `Places` with `Leiria` inside it, and `Óbidos` inside
that. Both images should appear under every leaf.

Check specifically:

- [ ] `Places|Portugal|Leiria|Óbidos` nests three deep rather than appearing as
      one keyword with pipes in it
- [ ] `Óbidos` keeps its accent
- [ ] `Tags|Nature|Coast|beach` uses the controlled vocabulary's own branch
- [ ] the rating shows 4 stars
- [ ] the flat keywords are still present and not replaced by the tree
- [ ] the **sidecar** image gets the same tree as the embedded one

## Lightroom Classic

1. **File → Import Photos and Video**, choose the fixture directory, import by
   *Add* (no copy needed).
2. If the sidecar image comes in bare, select it and use
   **Metadata → Read Metadata from File**.
3. Open the **Keyword List** panel on the right.

Lightroom shows `HierarchicalSubject` as its keyword hierarchy; the disclosure
triangles should expand to the same four roots. The star rating appears in the
toolbar and in the Library grid.

Check specifically:

- [ ] the Keyword List shows nested keywords, not seven flat ones
- [ ] expanding `Places` reaches `Óbidos` three levels down, accent intact
- [ ] the rating reads 4 stars
- [ ] the **sidecar** image gets the same keywords as the embedded one

## Recording the result

Screenshots go in `docs/assets/interop/`, named for what they show:

```
docs/assets/interop/digikam-tag-tree.png
docs/assets/interop/lightroom-keyword-list.png
```

Then, on [#357](https://github.com/kurok/pyimgtag/issues/357): tick the
remaining acceptance criterion, attach the screenshots, and say which versions
of which applications you used — an import behaviour is a claim about a
version, not about the application forever.

If the tree does **not** appear as expected, that is a bug worth its own issue.
Include the exiftool dump, which separates "pyimgtag wrote it wrong" from "the
application reads it differently":

```bash
exiftool -G1 -XMP-lr:HierarchicalSubject -XMP-digiKam:TagsList \
    -XMP:Rating -XMP:Subject ~/Desktop/pyimgtag-interop/*.jpg \
    ~/Desktop/pyimgtag-interop/*.xmp
```

Until both applications are ticked off, the README describes the hierarchical
keywords as verified against exiftool and not against the applications, and
`pyimgtag export --format digikam` stays marked provisional. Those two callouts
come down together with this checklist.
