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

interface VersionedPayload {
	version: 2;
	data: TranscriptionResult;
}

const STORAGE_KEY = "dialogscribe_transcription";
const STORAGE_VERSION = 2;

function isSegment(value: unknown): value is TranscriptionSegment {
	if (typeof value !== "object" || value === null) return false;
	const seg = value as Record<string, unknown>;
	return (
		typeof seg.text === "string" &&
		typeof seg.start === "number" &&
		typeof seg.end === "number" &&
		Number.isFinite(seg.start) &&
		Number.isFinite(seg.end)
	);
}

function isTranscriptionResult(value: unknown): value is TranscriptionResult {
	if (typeof value !== "object" || value === null) return false;
	const result = value as Record<string, unknown>;
	return (
		typeof result.text === "string" &&
		Array.isArray(result.segments) &&
		result.segments.every(isSegment) &&
		typeof result.duration === "number" &&
		Number.isFinite(result.duration) &&
		typeof result.language === "string"
	);
}

function parseVersioned(raw: string): TranscriptionResult | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return null;
	}
	if (typeof parsed !== "object" || parsed === null) return null;
	const payload = parsed as Record<string, unknown>;
	if (payload.version !== STORAGE_VERSION || !isTranscriptionResult(payload.data)) {
		return null;
	}
	return payload.data;
}

function loadFromStorage(): TranscriptionResult | null {
	if (typeof sessionStorage === "undefined") return null;
	try {
		const raw = sessionStorage.getItem(STORAGE_KEY);
		return raw ? parseVersioned(raw) : null;
	} catch {
		return null;
	}
}

function saveToStorage(value: TranscriptionResult | null): void {
	if (typeof sessionStorage === "undefined") return;
	try {
		if (value) {
			const payload: VersionedPayload = { version: STORAGE_VERSION, data: value };
			sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
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
