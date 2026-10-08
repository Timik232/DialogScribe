import { goto } from "$app/navigation";
import { authStore } from "$lib/stores/auth";

type FetchOptions = RequestInit & { skipAuthRedirect?: boolean };

function getAccessToken(): string {
	let token = "";
	authStore.subscribe((s) => (token = s.accessToken))();
	return token;
}

async function fetchWithRefresh(
	method: string,
	path: string,
	options: FetchOptions
): Promise<Response> {
	const { skipAuthRedirect, ...init } = options;

	const headers: Record<string, string> = {
		...(init.headers as Record<string, string>),
	};
	const token = getAccessToken();
	if (token) {
		headers["Authorization"] = `Bearer ${token}`;
	}

	let res = await fetch(path, {
		...init,
		method,
		headers,
		credentials: "include",
	});

	if (res.status === 401 && token && !skipAuthRedirect) {
		const newToken = await authStore.refresh();
		if (newToken) {
			headers["Authorization"] = `Bearer ${newToken}`;
			res = await fetch(path, { ...init, method, headers, credentials: "include" });
		}
	}

	if (res.status === 401 && !skipAuthRedirect) {
		const return_url = window.location.pathname + window.location.search;
		goto(`/login?return_url=${encodeURIComponent(return_url)}`);
		throw new Error("Unauthorized");
	}

	return res;
}

function extractApiError(status: number, text: string): Error {
	try {
		const detail = JSON.parse(text)?.detail?.error;
		if (detail?.message) {
			return new Error(detail.message);
		}
	} catch {
		return new Error(`API error ${status}: ${text}`);
	}
	return new Error(`API error ${status}: ${text}`);
}

export async function fetchApi<T = unknown>(
	method: string,
	path: string,
	options: FetchOptions = {}
): Promise<T> {
	const res = await fetchWithRefresh(method, path, options);

	if (!res.ok) {
		const text = await res.text().catch(() => "");
		throw extractApiError(res.status, text);
	}

	const contentType = res.headers.get("content-type");
	if (contentType?.includes("application/json")) {
		return res.json() as Promise<T>;
	}

	return undefined as T;
}

/** Like fetchApi, but returns a Blob (file downloads) with the same 401 -> refresh -> retry semantics. */
export async function fetchApiBlob(
	method: string,
	path: string,
	options: FetchOptions = {}
): Promise<Blob> {
	const res = await fetchWithRefresh(method, path, options);

	if (!res.ok) {
		throw new Error(`API ${method} ${path} failed: ${res.status}`);
	}

	return res.blob();
}
