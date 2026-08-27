# AI Rules for WZ/XML Tools / WZ 与 XML 工具 AI 规范

## Raw Round-Trip First / 原样往返优先

- The first validation target is byte-level round-trip: export WZ to XML,
  make no edits, pack XML back to WZ, then compare MD5 with the original.
- 当前第一验证目标是字节级往返：WZ 导出 XML，不做修改，再打包回 WZ，
  然后和原文件做 MD5 对比。
- Exported XML must represent the original parsed WZ data and also preserve the
  original `.img` body bytes needed for no-edit round-trip packing.
- 导出的 XML 必须表达原 WZ 解析出来的数据，同时保留未修改往返打包所需的
  `.img` 原始 body 字节。
- XML packing must default to rebuilding valid `<imgdir>` files from XML node
  content. Exported raw body backup data may be used only by an explicit raw
  round-trip option, because otherwise user edits would be ignored.
- XML 打包默认必须按合法 `<imgdir>` 的 XML 节点内容重建。导出的原始 body 备份数据
  只能通过显式原样往返选项使用，否则会导致用户修改被忽略。
- When rebuilding an image body, recalculate that image directory entry's
  checksum from the actual output bytes. Do not reuse stale exported checksums
  after XML edits.
- 重建 `.img` body 时，必须按实际输出字节重新计算该 image 目录项 checksum。
  XML 修改后不得复用导出时的旧 checksum。
- Do not repair, normalize, reinterpret, or replace source data because it looks
  invalid, inconvenient, or not server/client friendly.
- 不允许因为数据看起来不合法、不方便、或不适合服务端/客户端，就擅自修复、
  归一化、重解释或替换原数据。
- Packing must use the XML/file content as provided. It must not reject content
  merely because a node is missing fields, has strange values, or may not run.
- 打包时必须按给定 XML/文件内容写入。不得仅因为节点缺字段、值奇怪、或可能无法运行
  就拒绝。
- If a `.xml` file cannot be parsed as supported node XML, pack that file's raw
  bytes into the corresponding `.img` body instead of trying to make it valid.
- 如果某个 `.xml` 不能解析成当前支持的节点 XML，就把该文件原始字节写进对应
  `.img` body，不要尝试把它修成合法数据。
- Raw canvas XML must keep `format` and `format2` as separate fields when those
  fields are exported. Do not merge them into one display-oriented value.
- 导出 canvas 原字段时，`format` 和 `format2` 必须分开保存，不要合并成面向显示的
  单个值。
- Logs should contain only warnings/errors. Normal progress belongs in the
  console output.
- 日志只记录 warning/error。正常进度只输出到控制台。
