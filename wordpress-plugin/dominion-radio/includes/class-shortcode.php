<?php
/**
 * Dominion Radio shortcode.
 *
 * Renders the [dominion_radio] player markup and enqueues the frontend assets.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Dominion_Radio_Shortcode {

	const REST_PATH = '/radio';

	/* ------------------------------------------------------------------
	 * Render
	 * ---------------------------------------------------------------- */

	public static function render( $atts = array() ) {
		$atts = shortcode_atts(
			array(
				'name'        => Dominion_Radio::station_name(),
				'label'       => 'Dominion Radio',
				'tagline'     => 'Worship, the Word and prayer, wherever you are.',
				'autoplay'    => 'no',
				'offline'     => '',
			),
			$atts,
			'dominion_radio'
		);

		$backend_url = trim( (string) Dominion_Radio::backend_url() );
		$name        = '' !== trim( (string) $atts['name'] ) ? (string) $atts['name'] : Dominion_Radio::station_name();
		$label       = '' !== trim( (string) $atts['label'] ) ? (string) $atts['label'] : 'Dominion Radio';
		$tagline     = '' !== trim( (string) $atts['tagline'] ) ? (string) $atts['tagline'] : 'A Sound of Worship';
		$autoplay    = in_array( strtolower( (string) $atts['autoplay'] ), array( 'yes', '1', 'true' ), true );
		$offline_msg = '' !== trim( (string) $atts['offline'] ) ? (string) $atts['offline'] : sprintf(
			/* translators: %s: station name */
			__( '%s is not on air right now.', 'dominion-radio' ),
			$name
		);

		self::enqueue_assets();

		// Determine the Apostle photograph URL from the live website media library.
		$apostle_image = self::get_apostle_image_url();

		ob_start();
		?>
		<div class="dr-radio"
			data-backend-url="<?php echo esc_attr( $backend_url ); ?>"
			data-autoplay="<?php echo $autoplay ? '1' : '0'; ?>"
			data-name="<?php echo esc_attr( $name ); ?>"
			data-label="<?php echo esc_attr( $label ); ?>"
			data-tagline="<?php echo esc_attr( $tagline ); ?>"
			data-offline-msg="<?php echo esc_attr( $offline_msg ); ?>">

			<?php require DOMINION_RADIO_PATH . 'templates/radio-player.php'; ?>

		</div>
		<?php
		return ob_get_clean();
	}

	/* ------------------------------------------------------------------
	 * Assets
	 * ---------------------------------------------------------------- */

	public static function enqueue_assets() {
		// Fonts: Inter + Playfair Display (same as Minister Live).
		wp_enqueue_style(
			'dominion-radio-fonts',
			'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@500;600;700&display=swap',
			array(),
			null
		);

		wp_enqueue_style(
			'dominion-radio',
			DOMINION_RADIO_URL . 'assets/css/dominion-radio.css',
			array(),
			DOMINION_RADIO_VERSION
		);

		wp_enqueue_script(
			'dominion-radio',
			DOMINION_RADIO_URL . 'assets/js/dominion-radio.js',
			array(),
			DOMINION_RADIO_VERSION,
			true
		);
	}

	/* ------------------------------------------------------------------
	 * Helpers
	 * ---------------------------------------------------------------- */

	private static function get_apostle_image_url() {
		// The Apostle's poster from the live website media library.
		// Verified from the homepage hero data-tablet-portrait-src.
		return 'https://toluwalogoagboola.org/wp-content/uploads/2026/10/Apostle-Toluwalogo-Agboola-Poster-1.png';
	}
}
