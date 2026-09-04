import { goto } from "$app/navigation";
import { authStore } from "$lib/stores/auth";

type FetchOptions = RequestInit & { skipAuthRedirect?: boolean };

function getAccessToken(): string {
	let token = "";
	authStore.subscribe((s) => (token = s.accessToken))();
	return token;
}

async function refreshAndGetToken(): Promise<string | null> {
	const newToken = await authStore.refresh();
	return newToken;
}

export async function fetchApi<T = unknown>(
	method: string,
	path: string,
	options: FetchOptions = {}
): Promise<T> {
	const { skipAuthRedirect, ...init } = options;

	const token = getAccessToken();
	const headers: Record<string, string> = {
		...(init.headers as Record<string, string>),
	};
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
		const newToken = await refreshAndGetToken();
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

	if (!res.ok) {
		const text = await res.text().catch(() => "");
		throw new Error(`API ${method} ${path} failed: ${res.status} ${text}`);
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
	const { skipAuthRedirect, ...init } = options;

	const token = getAccessToken();
	const headers: Record<string, string> = {
		...(init.headers as Record<string, string>),
	};
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
		const newToken = await refreshAndGetToken();
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

	if (!res.ok) {
		throw new Error(`API ${method} ${path} failed: ${res.status}`);
	}

	return res.blob();
}
