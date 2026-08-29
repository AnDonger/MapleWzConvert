# XML Recursive Formatter

Format every `.xml` file in a directory, including files in subdirectories, and save the formatted files to another directory while preserving the original relative paths.

## Usage

## Export GMS v083 WZ to Server XML

Install dependencies first:

```powershell
python -m pip install -r requirements.txt
```

Export a single WZ file:

```powershell
python wz_to_xml.py ./original_wz/Map.wz ./out_wz_to_xml
```

Export every `.wz` file under a client folder:

```powershell
python wz_to_xml.py ./original_wz ./out_wz_to_xml
```

The script writes paths like:

```text
out_wz_to_xml\Character.wz\Accessory\01010000.img.xml
out_wz_to_xml\Map.wz\Map\Map0\000000000.img.xml
```

Defaults are `--region GMS --version 83`. Use `--auto-version` if the file
does not open with version 83.

Canvas nodes keep the parsed WZ values exactly as read, including `width`,
`height`, `format`, and `format2`, and write the original canvas payload as
`rawdata` plus `rawlength`. The script does not decode canvas data to PNG and
does not infer or repair canvas dimensions.
After parser changes, previously exported XML should be exported again before
packing; old XML keeps whatever canvas bytes were written by that earlier run.

Each exported `.img.xml` root only adds `wz_rawlength`, the original `.img`
body byte length, and `wz_xmlsha256`, a hash of the editable XML nodes. These
two attributes are export metadata and are not original WZ node fields.

Each export creates error logs under `<output>\_logs`:

```text
_logs\wz_export_YYYYMMDD_HHMMSS.log
_logs\wz_export_latest.log
```

Normal progress is printed to the console only. Log files contain only
`WARNING`, `ERROR`, and a `SUMMARY` when there was at least one warning or
error. If a run has no problems, the latest log is empty.

Use the latest log when checking the newest run:

```powershell
Select-String ./out_wz_to_xml/_logs/wz_export_latest.log" -Pattern "\[WARNING\]|\[ERROR\]|\[SUMMARY\]"
```

Canvas images are not decoded, so PNG decode warnings are not produced.

## Pack XML Folder Back to WZ

Pack a folder such as `Map.wz` back into a legacy `.wz` file:

```powershell
python xml_to_wz.py ./out_wz_to_xml/Map.wz ./out_xml_to_wz/Map.wz
```

The packer rebuilds valid `<imgdir>` XML from node content so edited values,
added nodes, and deleted nodes are written into the new WZ. Export metadata on
the root `<imgdir>`, such as `wz_rawlength` and `wz_xmlsha256`, is ignored
during packing and is not written as a WZ node.

When an image body is rebuilt, the packer writes the directory entry checksum
from the actual output body bytes instead of reusing the exported old checksum.

The packer does not reject XML content merely because fields are missing,
strange, or not runnable. If a `.xml` file cannot be parsed as supported node
XML, the file's raw bytes are packed as that `.img` body. Normal progress is
printed to the console; pack logs under `_logs` contain only warnings/errors.

## Export WZ to Raw IMG

Export every embedded `.img` body from a `.wz` file without parsing or changing
the image body bytes:

```powershell
python wz_to_img.py ./original_wz/Map.wz ./out_wz_to_img
```

The script writes paths like:

```text
out_wz_to_img\Map.wz\Tile\ancientForest.img
out_wz_to_img\Map.wz\Map\Map0\000000000.img
```

Normal progress is printed to the console; logs only contain warnings/errors.

## Pack Raw IMG Folder Back to WZ

Pack a folder such as `wz_to_img/Map.wz` back into a `.wz` file:

```powershell
python img_to_wz.py ./out_wz_to_img/Map.wz ./out_img_to_wz/Map.wz
```

This packer reads each `.img` file as raw bytes. It does not parse, validate,
repair, normalize, or reinterpret `.img` content before writing it into the WZ.

## Export XML Canvas to PNG

Export one `<canvas>` node's `rawdata` from an exported `.img.xml` file to PNG:

```powershell
python xml_canvas_to_png.py ./out_wz_to_xml/Map.wz/Obj/login.img.xml ./ai_test_data/login_logo.png Title/logo/0/0
```

The third argument is the node path by `name` from the XML root. If the output
argument is a directory, the script writes a PNG named from the node path.

## Write PNG to XML Canvas

Write a PNG back into one `<canvas>` node's `rawdata` in an exported `.img.xml`
file:

```powershell
python png_to_xml_canvas.py ./ai_test_data/login_Title_logo_0_1.png ./out_wz_to_xml/Map.wz/Obj/login.img.xml Title/logo/0/1
```

The script keeps the XML file in place and only changes the target canvas start
tag's `rawlength` and `rawdata` attributes. By default, the PNG size must match
the canvas `width` and `height`; pass `--resize` if you explicitly want the PNG
scaled to that canvas size.

## Replace English XML Text with Chinese XML Text

Replace only matching `<string name="..." value="..."/>` values from Chinese
XML into English XML while preserving the English directory layout:

```powershell
python en_to_cn_xml.py ./pack_en_xml ./pack_cn_xml ./pack_en_to_cn_xml
```

Matching uses the same relative XML path plus the same `imgdir` path and
`string name`. The English XML is kept as the base file; the script only
replaces the matched `value` attribute text and does not reformat XML, rewrite
empty tags, change unrelated fields, or change line endings.

Only Chinese `value` text that contains real Han characters is used. Values
that look like garbled text such as `????` are skipped and treated as missing.

`String.wz/Skill.img.xml` has extra display handling for the old client/tool UI.
Long `desc` segments are wrapped with literal `\n`, preferring punctuation such
as `，` or `。` as the line break point when it does not increase the total line
count, and falling back to a 17-character limit. `h1`, `h2`, `h3` style hint
values are dynamically padded with one trailing space for each Chinese/full-width
character in the value. This special handling is only applied to `Skill.img.xml`.

Files and nodes missing from the Chinese XML are listed in:

```text
out_en_to_cn_xml\missing_cn_string_values.txt
```

The missing report is grouped by WZ and XML file, and only lists the unmatched
`<imgdir name="...">` entries, for example:

```text
Quest.wz
	Quest.wz/A.img.xml
		<imgdir name="123456">
```
