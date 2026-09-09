import { useLayoutEffect } from "react";

const unsavedEditors = new Set<object>();

export function confirmDiscardChanges(): boolean {
  return (
    unsavedEditors.size === 0 ||
    window.confirm("Wiki 有未保存的内容，离开将丢失这些修改。确定离开吗？")
  );
}

export function useUnsavedChanges(dirty: boolean) {
  useLayoutEffect(() => {
    if (!dirty) return;
    const editor = {};
    unsavedEditors.add(editor);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      unsavedEditors.delete(editor);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [dirty]);
}
