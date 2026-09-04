const API_BASE = "";

export interface TokenResponse {
	access_token: string;
	token_type: string;
}

export interface UserInfo {
	user_id: string;
	username: string;
	email: string;
	role: string;
}

export async function login(loginValue: string, password: string): Promise<TokenResponse & { user: UserInfo }> {
	const res = await fetch(`${API_BASE}/api/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ login: loginValue, password }),
		credentials: "include",
	});

	const data = await res.json().catch(() => ({}));

	if (!res.ok) {
		const reason = data.detail?.reason || data.detail;
		const err: any = new Error(reason || "Login failed");
		err.reason = data.detail?.reason;
		throw err;
	}

	const tokenData: TokenResponse = data;
	const user = await getMe(tokenData.access_token);
	return { ...tokenData, user };
}

export async function register(email: string, username: string, password: string): Promise<void> {
	const res = await fetch(`${API_BASE}/api/auth/register`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ email, username, password }),
		credentials: "include",
	});

	if (!res.ok) {
		const data = await res.json().catch(() => ({}));
		throw new Error(data.detail || "Registration failed");
	}
}

export async function refreshToken(): Promise<TokenResponse> {
	const res = await fetch(`${API_BASE}/api/auth/refresh`, {
		method: "POST",
		credentials: "include",
	});

	if (!res.ok) {
		throw new Error("Token refresh failed");
	}

	return res.json();
}

export async function logout(): Promise<void> {
	await fetch(`${API_BASE}/api/auth/logout`, {
		method: "POST",
		credentials: "include",
	});
}

export async function getMe(token: string): Promise<UserInfo> {
	const res = await fetch(`${API_BASE}/api/auth/me`, {
		headers: { Authorization: `Bearer ${token}` },
		credentials: "include",
	});

	if (!res.ok) {
		throw new Error("Failed to get user info");
	}

	return res.json();
}

export async function forgotPassword(email: string): Promise<void> {
	const res = await fetch(`${API_BASE}/api/auth/forgot-password`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ email }),
		credentials: "include",
	});

	if (!res.ok) {
		const data = await res.json().catch(() => ({}));
		throw new Error(data.detail || "Failed to send reset email");
	}
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
	const res = await fetch(`${API_BASE}/api/auth/reset-password`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ token, new_password: newPassword }),
		credentials: "include",
	});

	if (!res.ok) {
		const data = await res.json().catch(() => ({}));
		throw new Error(data.detail || "Failed to reset password");
	}
}
