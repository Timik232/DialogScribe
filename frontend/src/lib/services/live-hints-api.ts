import { authStore } from "$lib/stores/auth";

function getAccessToken(): string {
	let token = "";
	authStore.subscribe((s) => (token = s.accessToken))();
	return token;
}

export function getLiveHintsWsUrl(): string {
	const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
	return `${protocol}//${window.location.host}/api/live-hints/ws`;
}

export class LiveHintsClient {
	private ws: WebSocket | null = null;
	private reconnectAttempts = 0;
	private maxReconnectAttempts = 3;
	private lastConfig?: { templateKey: string; contextText: string };
	private lastBrief?: { goal?: string; offering?: string; red_lines?: string; known_objections?: string };

	onTranscript: (segment: Record<string, unknown>) => void = () => {};
	onHints: (hints: Record<string, unknown>[]) => void = () => {};
	onError: (error: Record<string, unknown>) => void = () => {};
	onStatus: (status: Record<string, unknown>) => void = () => {};
	onReconnecting: () => void = () => {};
	onReconnectFailed: () => void = () => {};
	onFeedbackAck: (ack: { hint_id: string; status: string }) => void = () => {};

	connect(token: string): Promise<void> {
		const wsUrl = getLiveHintsWsUrl() + "?token=" + token;
		this.ws = new WebSocket(wsUrl);

		return new Promise((resolve, reject) => {
			if (!this.ws) {
				reject(new Error("WebSocket creation failed"));
				return;
			}

			this.ws.onopen = () => {
				this.reconnectAttempts = 0;
				resolve();
			};

			this.ws.onerror = (event) => {
				reject(new Error("WebSocket connection error"));
			};

			this.ws.onmessage = (event) => {
				try {
					const data = JSON.parse(event.data);
					switch (data.type) {
						case "transcript":
							this.onTranscript(data);
							break;
						case "hint":
							this.onHints([data]);
							break;
						case "hints":
							this.onHints(data.hints ?? data);
							break;
						case "error":
							this.onError(data);
							break;
						case "status":
							this.onStatus(data);
							break;
						case "feedback_ack":
							this.onFeedbackAck(data);
							break;
					}
				} catch {
					/* malformed JSON — skip */
				}
			};

			this.ws.onclose = () => {
				if (this.reconnectAttempts < this.maxReconnectAttempts) {
					this.reconnect();
				}
			};
		});
	}

	sendConfig(templateKey: string, contextText: string): void {
		this.lastConfig = { templateKey, contextText };
		this.ws?.send(
			JSON.stringify({
				type: "session_config",
				template_key: templateKey,
				context_text: contextText,
			}),
		);
	}

	sendBriefUpdate(brief: { goal?: string; offering?: string; red_lines?: string; known_objections?: string }): void {
		this.lastBrief = brief;
		this.ws?.send(
			JSON.stringify({
				type: "brief_update",
				...brief,
			}),
		);
	}

	sendHintFeedback(hintId: string, rating: "like" | "dislike"): void {
		this.ws?.send(
			JSON.stringify({
				type: "hint_feedback",
				hint_id: hintId,
				rating: rating,
			}),
		);
	}

	sendAudioChunk(audioB64: string, source: "mic" | "tab"): void {
		this.ws?.send(
			JSON.stringify({
				type: "audio_chunk",
				audio_b64: audioB64,
				source: source,
			}),
		);
	}

	disconnect(): void {
		this.reconnectAttempts = this.maxReconnectAttempts;
		this.ws?.close();
		this.ws = null;
	}

	reconnect(): void {
		if (this.reconnectAttempts >= this.maxReconnectAttempts) {
			this.onReconnectFailed();
			return;
		}

		this.onReconnecting();
		const delay = Math.pow(2, this.reconnectAttempts) * 1000;
		this.reconnectAttempts++;

		setTimeout(() => {
			const token = getAccessToken();
			if (token) {
				this.connect(token).then(() => {
					if (this.lastConfig) this.sendConfig(this.lastConfig.templateKey, this.lastConfig.contextText);
					if (this.lastBrief) this.sendBriefUpdate(this.lastBrief);
				}).catch(() => {
					this.onError({ message: "Reconnection failed" });
				});
			} else {
				this.onError({ message: "No auth token for reconnection" });
			}
		}, delay);
	}
}
