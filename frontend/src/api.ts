const TOKEN_KEY = 'usi.apiToken';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode) — token lives for this page only */
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function parse(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (form) payload = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload });
  const text = await response.text();
  const data = text ? parse(text) : null;
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
    const message =
      typeof detail === 'string'
        ? detail
        : detail && typeof detail === 'object' && 'message' in detail
          ? String((detail as { message: unknown }).message)
          : Array.isArray(detail)
            ? detail.map((d) => d.msg ?? JSON.stringify(d)).join('; ')
            : `Request failed (HTTP ${response.status})`;
    throw new ApiError(response.status, message, detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  put: <T>(path: string, body: unknown = {}) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown = {}) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, undefined, form),
};

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
