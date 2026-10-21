import { fetchApi } from "$lib/services/api";

export interface JobSegment {
	text: string;
	start: number;
	end: number;
	speaker?: string;
	confidence?: number;
}

export interface JobStatus {
	id: string;
	status: "queued" | "processing" | "completed" | "failed" | "cancelled";
	stage: string;
	progress: number;
	filename?: string | null;
	created_at?: string | null;
	finished_at?: string | null;
	error?: string | null;
	saved_transcription_id?: string | null;
	result?: {
		text: string;
		segments: JobSegment[];
		duration: number;
		language: string;
	} | null;
}

export type JobEventHandler = (job: JobStatus) => void;

export interface PollerOptions {
	/** Milliseconds between polls while active; grows slowly when hidden. */
	intervalMs?: number;
	/** Hard cap for the backoff interval when the tab is hidden. */
	maxIntervalMs?: number;
	onUpdate: JobEventHandler;
	onError?: (err: unknown) => void;
}

/**
 * Polls a background transcription job until it reaches a terminal state.
 *
 * Design notes (docs/design/background-jobs.md): short polling requests
 * survive every proxy idle timeout and page reloads, unlike long-lived
 * HTTP responses or WebSockets on mobile. When the tab becomes hidden the
 * interval backs off (mobile OSes throttle background timers anyway);
 * returning to the tab triggers an immediate poll so the user sees the
 * result the moment they come back.
 */
export class JobPoller {
	private timer: ReturnType<typeof setTimeout> | null = null;
	private cancelled = false;
	private readonly intervalMs: number;
	private readonly maxIntervalMs: number;
	private readonly onUpdate: JobEventHandler;
	private readonly onError?: (err: unknown) => void;

	constructor(private readonly jobId: string, opts: PollerOptions) {
		this.intervalMs = opts.intervalMs ?? 2500;
		this.maxIntervalMs = opts.maxIntervalMs ?? 15000;
		this.onUpdate = opts.onUpdate;
		this.onError = opts.onError;
	}

	start(): void {
		this.cancelled = false;
		document.addEventListener("visibilitychange", this.onVisibility);
		this.poll();
	}

	stop(): void {
		this.cancelled = true;
		document.removeEventListener("visibilitychange", this.onVisibility);
		if (this.cancelled) {
			if (this.timer) clearTimeout(this.timer);
			this.timer = null;
		}
	}

	private onVisibility = (): void => {
		if (!this.cancelled && document.visibilityState === "visible") {
			if (this.timer) clearTimeout(this.timer);
			this.poll();
		}
	};

	private currentInterval(): number {
		return document.visibilityState === "hidden"
			? Math.min(this.intervalMs * 3, this.maxIntervalMs)
			: this.intervalMs;
	}

	private async poll(): Promise<void> {
		if (this.cancelled) return;
		let job: JobStatus | null = null;
		try {
			job = await fetchApi<JobStatus>("GET", `/api/jobs/${this.jobId}`);
		} catch (err) {
			this.onError?.(err);
		}
		if (this.cancelled) return;
		if (job) {
			this.onUpdate(job);
			if (isTerminal(job)) return;
		}
		this.timer = setTimeout(() => void this.poll(), this.currentInterval());
	}
}

export function isTerminal(job: JobStatus): boolean {
	return job.status === "completed" || job.status === "failed" || job.status === "cancelled";
}

export const STAGE_LABELS: Record<string, string> = {
	upload: "Загрузка файла",
	transcribing: "Транскрибация",
	saving: "Сохранение результата",
	done: "Готово",
	error: "Ошибка",
	cancelled: "Отменено",
};

export function stageLabel(stage: string): string {
	return STAGE_LABELS[stage] ?? stage;
}
