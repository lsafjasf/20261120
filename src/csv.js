// Small CSV serializer for arrays of flat record objects.
export function toCsv(rows) {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const escape = (value) => {
    const text = String(value);
    return /[",\n]/.test(text)
      ? `"${text.replaceAll('"', '""')}"`
      : text;
  };
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(row[h])).join(","));
  }
  return lines.join("\n") + "\n";
}
