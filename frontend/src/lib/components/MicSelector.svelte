<script lang="ts">
	interface Props {
		selectedDeviceId?: string;
		disabled?: boolean;
	}

	let { selectedDeviceId = $bindable(""), disabled = false }: Props = $props();

	let devices: MediaDeviceInfo[] = $state([]);

	$effect(() => {
		const updateDevices = async () => {
			try {
				const allDevices = await navigator.mediaDevices.enumerateDevices();
				devices = allDevices.filter(d => d.kind === 'audioinput');
			} catch {
				devices = [];
			}
		};

		navigator.mediaDevices.addEventListener('devicechange', updateDevices);
		updateDevices();

		return () => {
			navigator.mediaDevices.removeEventListener('devicechange', updateDevices);
		};
	});
</script>

<div class="mic-selector">
	<select class="input" bind:value={selectedDeviceId} disabled={disabled}>
		<option value="">Системный по умолчанию</option>
		{#each devices as device, i}
			<option value={device.deviceId}>
				{device.label || `Микрофон ${i + 1}`}
			</option>
		{/each}
	</select>
</div>

<style>
	.mic-selector {
		display: inline-flex;
		align-items: center;
	}

	.mic-selector select.input {
		font-size: var(--font-size-sm, 0.8125rem);
		padding: 0.375rem 0.5rem;
		min-width: 160px;
		background-color: var(--color-card);
		color: var(--color-text);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		cursor: pointer;
	}

	.mic-selector select.input:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.mic-selector select.input:focus {
		outline: none;
		border-color: var(--color-cta);
	}
</style>
