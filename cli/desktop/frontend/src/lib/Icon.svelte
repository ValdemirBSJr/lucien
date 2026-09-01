<script lang="ts">
  export let path: string;
  export let size: number = 20;
</script>

<svg
  viewBox="0 0 24 24"
  width={size}
  height={size}
  fill="currentColor"
  aria-hidden="true"
  focusable="false"
>
  <path d={path} />
</svg>
