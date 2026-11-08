<?php
/**
 * Dominion Radio player template.
 *
 * Renders inside .dr-radio. All dynamic behaviour is handled by the
 * frontend JavaScript (assets/js/dominion-radio.js) which polls the
 * Python backend /radio/now endpoint. All IDs are replaced with classes
 * so multiple shortcode instances on one page are safe.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

$backend_url = esc_attr( isset( $backend_url ) ? $backend_url : '' );
$autoplay    = esc_attr( isset( $autoplay ) ? $autoplay : '0' );
$name        = esc_attr( isset( $name ) ? $name : 'Dominion Radio' );
$label       = esc_attr( isset( $label ) ? $label : 'Dominion Radio' );
$tagline     = esc_attr( isset( $tagline ) ? $tagline : 'A Sound of Worship' );
$offline_msg = esc_attr( isset( $offline_msg ) ? $offline_msg : '' );
$apostle_img = esc_url( isset( $apostle_image ) ? $apostle_image : '' );
?>

<!-- ============================================================
     Dominion Radio — Hero / Station Identity
     ============================================================ -->
<section class="dr-hero" aria-label="<?php echo $name; ?>">
	<div class="dr-hero__overlay"></div>
	<?php if ( $apostle_img ): ?>
		<img src="<?php echo $apostle_img; ?>" alt="<?php echo esc_attr( $name ); ?>" class="dr-hero__image" loading="lazy" fetchpriority="high">
	<?php endif; ?>
	<div class="dr-hero__content">
		<span class="dr-hero__eyebrow"><?php echo esc_html( $label ); ?></span>
		<h1 class="dr-hero__title"><?php echo esc_html( $name ); ?></h1>
		<p class="dr-hero__tagline"><?php echo esc_html( $tagline ); ?></p>

		<div class="dr-hero__status">
			<span class="dr-hero__status-dot" aria-hidden="true"></span>
			<span class="dr-hero__status-text">Loading&hellip;</span>
		</div>
	</div>
</section>

<!-- ============================================================
     Dominion Radio — Player
     ============================================================ -->
<section class="dr-player" aria-label="Radio player">
	<div class="dr-player__card">

		<!-- Now playing info -->
		<div class="dr-player__info">
			<h2 class="dr-player__track-title">Select a track</h2>
			<p class="dr-player__performer"></p>
		</div>

		<!-- Visualizer placeholder -->
		<div class="dr-player__visualizer" aria-hidden="true">
			<div class="dr-visualizer__bar"></div>
			<div class="dr-visualizer__bar"></div>
			<div class="dr-visualizer__bar"></div>
			<div class="dr-visualizer__bar"></div>
			<div class="dr-visualizer__bar"></div>
		</div>

		<!-- Controls -->
		<div class="dr-player__controls">
			<button type="button" class="dr-btn dr-btn--play" aria-label="Play" aria-pressed="false">
				<svg class="dr-icon dr-icon--play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
				<svg class="dr-icon dr-icon--pause" viewBox="0 0 24 24" aria-hidden="true" hidden><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>
			</button>

			<div class="dr-player__progress-wrap">
				<div class="dr-player__progress">
					<div class="dr-player__progress-fill"></div>
					<div class="dr-player__progress-handle"></div>
				</div>
				<div class="dr-player__times">
					<span class="dr-player__time">0:00</span>
					<span class="dr-player__time">0:00</span>
				</div>
			</div>

			<button type="button" class="dr-btn dr-btn--mute" aria-label="Mute" aria-pressed="false">
				<svg class="dr-icon dr-icon--vol" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/></svg>
				<svg class="dr-icon dr-icon--muted" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg>
			</button>
		</div>

		<!-- Next track -->
		<div class="dr-player__next">
			<span class="dr-player__next-label">Up next</span>
			<span class="dr-player__next-title"></span>
		</div>

		<!-- Station stats -->
		<div class="dr-player__stats">
			<span class="dr-player__stat">-- tracks</span>
			<span class="dr-player__stat-separator">&middot;</span>
			<span class="dr-player__stat">--</span>
		</div>

	</div>

	<!-- Offline / error state -->
	<div class="dr-offline" hidden>
		<div class="dr-offline__icon" aria-hidden="true">
			<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/></svg>
		</div>
		<p class="dr-offline__text"><?php echo esc_html( $offline_msg ); ?></p>
		<p class="dr-offline__sub"></p>
		<button type="button" class="dr-btn dr-btn--main">Try again</button>
	</div>

	<!-- Hidden audio element -->
	<audio class="dr-audio" preload="none" crossorigin="anonymous"></audio>
</section>

<!-- ============================================================
     Dominion Radio — Playlist
     ============================================================ -->
<section class="dr-playlist" aria-label="Playlist">
	<h2 class="dr-playlist__heading">
		<span class="dr-playlist__eyebrow">Playlist</span>
		<span class="dr-playlist__count">--</span>
	</h2>
	<div class="dr-playlist__list">
		<!-- Tracks injected by JavaScript -->
	</div>
	<div class="dr-playlist__empty" hidden>
		<p>No tracks available yet.</p>
	</div>
</section>
