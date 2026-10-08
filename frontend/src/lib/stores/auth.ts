import { writable } from "svelte/store";
import { login as apiLogin, register as apiRegister, logout as apiLogout, refreshToken } from "$lib/services/auth";

export interface AuthState {
	accessToken: string;
	user: { user_id: string; username: string; email: string; role: string } | null;
	isAuthenticated: boolean;
	loading: boolean;
}

function createAuthStore() {
	const { subscribe, set, update } = writable<AuthState>({
		accessToken: "",
		user: null,
		isAuthenticated: false,
		loading: true,
	});

	async function login(loginValue: string, password: string): Promise<void> {
		const data = await apiLogin(loginValue, password);
		set({
			accessToken: data.access_token,
			user: data.user,
			isAuthenticated: true,
			loading: false,
		});
	}

	async function register(email: string, username: string, password: string): Promise<void> {
		await apiRegister(email, username, password);
	}

	async function logout(): Promise<void> {
		try {
			await apiLogout();
		} finally {
			set({
				accessToken: "",
				user: null,
				isAuthenticated: false,
				loading: false,
			});
		}
	}

	async function refresh(): Promise<string | null> {
		try {
			const data = await refreshToken();
			update((s) => ({
				...s,
				accessToken: data.access_token,
				isAuthenticated: true,
				loading: false,
			}));
			return data.access_token;
		} catch {
			set({
				accessToken: "",
				user: null,
				isAuthenticated: false,
				loading: false,
			});
			return null;
		}
	}

	async function init(): Promise<void> {
		try {
			const token = await refresh();
			if (token) {
				const { getMe } = await import("$lib/services/auth");
				const user = await getMe(token);
				update((s) => ({ ...s, user }));
			}
		} catch {
			set({
				accessToken: "",
				user: null,
				isAuthenticated: false,
				loading: false,
			});
		}
	}

	init();

	return { subscribe, set, update, login, register, logout, refresh };
}

export const authStore = createAuthStore();
