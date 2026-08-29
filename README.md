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

Each exported `.img.xml` root also includes `wz_rawbody`, the original `.img`
body bytes from the WZ archive, and `wz_xmlsha256`, a hash of the editable XML
nodes. XML packing uses these per-image values to keep unmodified `.img`
bodies byte-for-byte intact while rebuilding only the `.img.xml` files whose
editable XML content changed. The packer does not copy the original `.wz` file
as the packing result.

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

By default, XML files exported by `wz_to_xml.py` are compared against
`wz_xmlsha256`. If the editable XML content is unchanged, the packer writes that
file's original `wz_rawbody`. If it changed, the packer rebuilds that `.img`
from the XML node content so edited values, added nodes, and deleted nodes are
written into the new WZ. `--use-wz-rawbody` is now only a forced raw mode for
debugging because it ignores XML edits in files that have `wz_rawbody`.

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
