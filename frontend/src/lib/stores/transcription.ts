import { writable } from "svelte/store";

export interface TranscriptionSegment {
	text: string;
	start: number;
	end: number;
	speaker?: string;
	confidence?: number;
}

export interface TranscriptionResult {
	text: string;
	segments: TranscriptionSegment[];
	duration: number;
	language: string;
	speaker_names?: Record<string, string>;
}

const STORAGE_KEY = "dialogscribe_transcription";

function loadFromStorage(): TranscriptionResult | null {
	if (typeof sessionStorage === "undefined") return null;
	try {
		const raw = sessionStorage.getItem(STORAGE_KEY);
		return raw ? JSON.parse(raw) : null;
	} catch {
		return null;
	}
}

function saveToStorage(value: TranscriptionResult | null): void {
	if (typeof sessionStorage === "undefined") return;
	try {
		if (value) {
			sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
		} else {
			sessionStorage.removeItem(STORAGE_KEY);
		}
	} catch {
		/* quota exceeded — ignore */
	}
}

function createTranscriptionStore() {
	const initial = loadFromStorage();
	const { subscribe, set } = writable<TranscriptionResult | null>(initial);

	function put(value: TranscriptionResult): void {
		saveToStorage(value);
		set(value);
	}

	function clear(): void {
		saveToStorage(null);
		set(null);
	}

	return { subscribe, set: put, clear };
}

export const transcriptionStore = createTranscriptionStore();
