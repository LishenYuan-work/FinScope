export const ACCESS_TOKEN_KEY = "finscope_access_token";

export function getAccessToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ACCESS_TOKEN_KEY) ?? "";
}

export function isGuestToken(token = getAccessToken()): boolean {
  return token.startsWith("guest_");
}

export function saveAccessToken(token: string) {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken() {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
}

export function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

export async function authFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(input, { ...init, headers: authHeaders(init.headers) });
  if (response.status === 401 && typeof window !== "undefined") {
    clearAccessToken();
    window.location.assign("/login");
  }
  return response;
}
