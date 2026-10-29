export function appUrl(path: string, baseUrl: string | URL = document.baseURI): URL {
  const relativePath = path.replace(/^\/+/, "");
  return new URL(relativePath, baseUrl);
}

export function appScopePath(baseUrl: string | URL = document.baseURI): string {
  return new URL(".", baseUrl).pathname;
}
