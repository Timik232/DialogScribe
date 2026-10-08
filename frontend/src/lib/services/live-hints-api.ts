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

const SOURCE_TAGS: Record<'mic' | 'tab', number> = { mic: 1, tab: 2 };

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
		const wsUrl = getLiveHintsWsUrl();
		this.ws = new WebSocket(wsUrl);
		this.ws.binaryType = 'arraybuffer';

		return new Promise((resolve, reject) => {
			if (!this.ws) {
				reject(new Error("WebSocket creation failed"));
				return;
			}

			let authed = false;

			this.ws.onopen = () => {
				this.ws?.send(JSON.stringify({ type: "auth", token, protocol: 1 }));
			};

			this.ws.onerror = () => {
				if (!authed) {
					reject(new Error("WebSocket connection error"));
				}
			};

			this.ws.onmessage = (event) => {
				try {
					const data = JSON.parse(event.data);
					if (!authed) {
						if (data.type === "auth_ok") {
							authed = true;
							this.reconnectAttempts = 0;
							resolve();
						}
						return;
					}
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

			this.ws.onclose = (event) => {
				if (!authed) {
					reject(new Error(`Live-hints auth failed (code ${event.code})`));
					return;
				}
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

	sendAudioChunk(audio: ArrayBuffer | Uint8Array, source: "mic" | "tab"): void {
		if (!this.ws) return;
		const bytes = audio instanceof Uint8Array ? audio : new Uint8Array(audio);
		if (bytes.length === 0) return;
		const framed = new Uint8Array(bytes.length + 1);
		framed[0] = SOURCE_TAGS[source];
		framed.set(bytes, 1);
		this.ws.send(framed);
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
