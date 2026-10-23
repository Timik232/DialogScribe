/**
 * Browser notifications + tab-title badge for finished background jobs.
 *
 * Notification permission must be requested from a user gesture (file upload
 * click), hence ensureNotificationPermission() is called on job start, not
 * at module load. The tab-title badge covers the denied/unsupported case.
 */

const TITLE_SUFFIX = '✅';

let notified = false;

export function ensureNotificationPermission(): void {
	if (typeof Notification === 'undefined') return;
	if (Notification.permission === 'default') {
		void Notification.requestPermission();
	}
}

function clearTitleBadge(): void {
	if (document.title.startsWith(`${TITLE_SUFFIX} `)) {
		document.title = document.title.slice(2);
	}
}

export function notifyJobFinished(kind: 'completed' | 'failed', detail?: string): void {
	if (notified) return;
	notified = true;

	if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) {
		const n = new Notification(
			kind === 'completed' ? 'Расшифровка готова' : 'Расшифровка не удалась',
			{
				body: detail ?? (kind === 'completed' ? 'Откройте вкладку, чтобы посмотреть результат' : 'Откройте вкладку для деталей'),
				tag: 'dialogscribe-job'
			}
		);
		n.onclick = () => {
			window.focus();
			n.close();
		};
	}

	if (document.hidden && !document.title.startsWith(`${TITLE_SUFFIX} `)) {
		document.title = `${TITLE_SUFFIX} ${document.title}`;
		document.addEventListener(
			'visibilitychange',
			() => {
				if (!document.hidden) clearTitleBadge();
			},
			{ once: true }
		);
	}
}

/** Reset the one-shot guard (e.g. before starting a new job). */
export function resetJobNotification(): void {
	notified = false;
	clearTitleBadge();
}
