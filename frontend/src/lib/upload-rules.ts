export const MAX_UPLOAD_BYTES = 10_000_000;

const EXTENSIONS = new Set(["pdf", "png", "jpg", "jpeg"]);
const MEDIA_TYPES = new Set(["application/pdf", "image/png", "image/jpeg"]);

export type UploadRejection = "type" | "size" | "count";

export function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index + 1).toLowerCase();
}

export function validateUpload(file: Pick<File, "name" | "type" | "size">): UploadRejection | null {
  const extension = extensionOf(file.name);
  const typeOk = MEDIA_TYPES.has(file.type) || (file.type === "" && EXTENSIONS.has(extension));
  const extensionOk = EXTENSIONS.has(extension);
  if (!typeOk || !extensionOk) return "type";
  if (file.size > MAX_UPLOAD_BYTES) return "size";
  return null;
}

export function uploadErrorMessage(name: string, reason: UploadRejection): string {
  if (reason === "size") return `"${name}" is larger than 10 MB.`;
  if (reason === "count") return "Choose one file.";
  return `"${name}" can't be uploaded. Use PDF, PNG, or JPEG.`;
}
