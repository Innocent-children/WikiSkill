import { useEffect, useState } from "react";

export function WikiSearch({
  value,
  onSearch,
}: {
  value: string;
  onSearch: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <form
      className="toolbar"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        onSearch(draft);
      }}
    >
      <label>
        搜索 Wiki
        <input
          type="search"
          placeholder="名称或当前正文"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <button type="submit">搜索</button>
      <button
        type="button"
        disabled={!draft && !value}
        onClick={() => {
          setDraft("");
          onSearch("");
        }}
      >
        清空搜索
      </button>
    </form>
  );
}
