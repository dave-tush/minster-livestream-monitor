<?php
/**
 * Dominion Radio player markup — v2.6.0 two-column ministry hero.
 *
 * Left: eyebrow, heading, description, ON AIR status, player (priority).
 * Right: Apostle portrait full-bleed at hero height (supporting).
 * Desktop/tablet: side by side, fitting a laptop screen without scroll.
 * Mobile: player first, portrait below.
 *
 * Variables supplied by Dominion_Radio_Shortcode::render():
 *   $instance_id, $backend, $atts (name, tagline, offline_message), $heading,
 *   $portrait, $can_manage.
 *
 * Track title / performer / duration / position / Up Next come from the
 * backend via JS — never hardcoded. All data-dr hooks retained.
 *
 * @package DominionRadio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

$dr_initial_state = '' === $backend ? 'config' : 'loading';

// Ministry editorial copy. $atts['tagline'] still honoured when customised;
// the legacy default is upgraded to the new description.
$dr_lead = isset( $atts['tagline'] ) ? (string) $atts['tagline'] : '';
if ( '' === $dr_lead || 'Worship, the Word and prayer, wherever you are.' === $dr_lead ) {
	$dr_lead = __( 'A continuous stream of sermons, worship and messages to strengthen your walk with God.', 'dominion-radio' );
}
$dr_eyebrow = ( 0 === strcasecmp( (string) $atts['name'], 'Dominion Radio' ) )
	? __( 'DOMINION RADIO · LIVE MINISTRY BROADCAST', 'dominion-radio' )
	: strtoupper( (string) $atts['name'] ) . ' · ' . __( 'LIVE MINISTRY BROADCAST', 'dominion-radio' );
?>
<div class="dr-radio" id="<?php echo esc_attr( $instance_id ); ?>" data-state="<?php echo esc_attr( $dr_initial_state ); ?>" data-backend-url="<?php echo esc_url( $backend ); ?>" data-name="<?php echo esc_attr( $atts['name'] ); ?>" data-offline-message="<?php echo esc_attr( $atts['offline_message'] ); ?>">
	<div class="dr-hero">

		<!-- Left column: identity, status, player (the listening experience). -->
		<div class="dr-hero__copy">
			<p class="dr-eyebrow"><?php echo esc_html( $dr_eyebrow ); ?></p>
			<h1 class="dr-title"><?php echo esc_html__( 'The Word. Worship. ', 'dominion-radio' ); ?><em><?php echo esc_html__( 'His Presence.', 'dominion-radio' ); ?></em></h1>
			<p class="dr-lead"><?php echo esc_html( $dr_lead ); ?></p>

			<span class="dr-onair" data-dr="onair">
				<span class="dr-onair__dot" aria-hidden="true"></span>
				<span class="dr-onair__text" data-dr="status"><?php echo 'config' === $dr_initial_state ? esc_html__( 'Not set up', 'dominion-radio' ) : esc_html__( 'Connecting', 'dominion-radio' ); ?></span>
			</span>

			<section class="dr-player" aria-label="<?php echo esc_attr( sprintf( /* translators: %s: station name */ __( '%s player', 'dominion-radio' ), $atts['name'] ) ); ?>">

				<div class="dr-notice" data-dr="notice" hidden>
					<p class="dr-notice__title" data-dr="notice-title"></p>
					<p class="dr-notice__sub" data-dr="notice-sub"></p>
					<button type="button" class="dr-btn dr-btn--retry" data-dr="retry"><?php esc_html_e( 'Try Again', 'dominion-radio' ); ?></button>
				</div>

				<div class="dr-player__card" data-dr="card">
					<div class="dr-player__play">
						<button type="button" class="dr-btn dr-btn--play" data-dr="play" aria-label="<?php esc_attr_e( 'Listen Live', 'dominion-radio' ); ?>" disabled>
							<svg class="dr-icon dr-icon--play" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M8.2 4.9v14.2a.6.6 0 0 0 .92.5l11-7.1a.6.6 0 0 0 0-1l-11-7.1a.6.6 0 0 0-.92.5z"/></svg>
							<svg class="dr-icon dr-icon--pause" viewBox="0 0 24 24" aria-hidden="true" focusable="false" hidden><rect fill="currentColor" x="6.2" y="5" width="3.8" height="14" rx="1"/><rect fill="currentColor" x="14" y="5" width="3.8" height="14" rx="1"/></svg>
							<span class="dr-spinner" aria-hidden="true"></span>
							<span class="dr-play__label" data-dr="play-label"><?php esc_html_e( 'Listen Live', 'dominion-radio' ); ?></span>
						</button>
					</div>
					<div class="dr-player__track">
						<div class="dr-player__labelrow">
							<span class="dr-player__label"><?php esc_html_e( 'Now playing', 'dominion-radio' ); ?></span>
							<button type="button" class="dr-btn dr-btn--live" data-dr="golive" hidden><?php esc_html_e( 'Back to live', 'dominion-radio' ); ?></button>
						</div>
						<p class="dr-player__track-title" data-dr="title"><?php esc_html_e( 'Connecting…', 'dominion-radio' ); ?></p>
						<p class="dr-player__performer" data-dr="performer"></p>
					</div>
					<div class="dr-player__mute">
						<button type="button" class="dr-btn dr-btn--mute" data-dr="mute" aria-label="<?php esc_attr_e( 'Mute', 'dominion-radio' ); ?>" aria-pressed="false">
							<svg class="dr-icon dr-icon--vol" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M4 9.5v5h3.4L12 18.5v-13L7.4 9.5z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M15.6 9.2a4 4 0 0 1 0 5.6M18.2 6.6a7.6 7.6 0 0 1 0 10.8"/></svg>
							<svg class="dr-icon dr-icon--muted" viewBox="0 0 24 24" aria-hidden="true" focusable="false" hidden><path fill="currentColor" d="M4 9.5v5h3.4L12 18.5v-13L7.4 9.5z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M16 9.6l4.8 4.8M20.8 9.6L16 14.4"/></svg>
						</button>
					</div>

					<input class="dr-seek" data-dr="seek" type="range" min="0" max="0" step="0.1" value="0" aria-label="<?php esc_attr_e( 'Seek within this track', 'dominion-radio' ); ?>" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0" aria-valuetext="" aria-disabled="true" disabled />

					<p class="dr-player__time" aria-hidden="true"><span data-dr="elapsed">0:00</span><span class="dr-player__time-sep"> / </span><span data-dr="total">0:00</span></p>

					<div class="dr-player__next" data-dr="next" hidden>
						<span class="dr-player__next-label"><?php esc_html_e( 'Up next', 'dominion-radio' ); ?></span>
						<span class="dr-player__next-title" data-dr="next-title"></span>
						<span class="dr-player__next-performer" data-dr="next-performer"></span>
					</div>
				</div>

				<p class="dr-sr" role="status" aria-live="polite" data-dr="announce"></p>
				<noscript><p class="dr-noscript"><?php esc_html_e( 'Please enable JavaScript to listen to Dominion Radio.', 'dominion-radio' ); ?></p></noscript>
				<?php if ( 'config' === $dr_initial_state && $can_manage ) : ?>
					<p class="dr-admin-hint"><?php echo esc_html__( 'Administrator note: set the backend URL under Settings → Dominion Radio. Visitors will see a "not available" message until then.', 'dominion-radio' ); ?></p>
				<?php endif; ?>
				<audio class="dr-audio" data-dr="audio" preload="none"></audio>
			</section>
		</div>

		<!-- Right column: portrait full-bleed at hero height (Itinerary reference). -->
		<figure class="dr-hero__figure">
			<?php if ( ! empty( $portrait['fallback'] ) ) : ?>
				<picture>
					<source srcset="<?php echo esc_url( $portrait['src'] ); ?>" type="image/webp" />
					<img class="dr-hero__img" src="<?php echo esc_url( $portrait['fallback'] ); ?>"<?php if ( $portrait['srcset'] ) : ?> srcset="<?php echo esc_attr( $portrait['srcset'] ); ?>" sizes="(max-width: 900px) 100vw, 42vw"<?php endif; ?> width="<?php echo (int) $portrait['width']; ?>" height="<?php echo (int) $portrait['height']; ?>" alt="<?php esc_attr_e( 'Apostle Toluwalogo Agboola', 'dominion-radio' ); ?>" decoding="async" fetchpriority="high" />
				</picture>
			<?php else : ?>
				<img class="dr-hero__img" src="<?php echo esc_url( $portrait['src'] ); ?>"<?php if ( $portrait['srcset'] ) : ?> srcset="<?php echo esc_attr( $portrait['srcset'] ); ?>" sizes="(max-width: 900px) 100vw, 42vw"<?php endif; ?> width="<?php echo (int) $portrait['width']; ?>" height="<?php echo (int) $portrait['height']; ?>" alt="<?php esc_attr_e( 'Apostle Toluwalogo Agboola', 'dominion-radio' ); ?>" decoding="async" fetchpriority="high" />
			<?php endif; ?>
		</figure>

	</div>
</div>
