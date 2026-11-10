<?php
/**
 * The [dominion_radio] shortcode: asset loading and template rendering.
 *
 * @package DominionRadio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Dominion_Radio_Shortcode {

	const TAG = 'dominion_radio';

	/** @var int Number of players rendered on this request (gives each a unique id). */
	private static $count = 0;

	/**
	 * Register the shortcode and assets.
	 */
	public static function init() {
		add_shortcode( self::TAG, array( __CLASS__, 'render' ) );
		add_action( 'wp_enqueue_scripts', array( __CLASS__, 'register_assets' ) );
		add_action( 'wp_enqueue_scripts', array( __CLASS__, 'maybe_enqueue_early' ), 20 );
	}

	/**
	 * Cache-busting version: plugin version + file modification time.
	 *
	 * @param string $relative Path inside the plugin.
	 * @return string
	 */
	private static function asset_version( $relative ) {
		$file = DOMINION_RADIO_DIR . $relative;
		return DOMINION_RADIO_VERSION . ( file_exists( $file ) ? '.' . filemtime( $file ) : '' );
	}

	/**
	 * Register (not enqueue) the stylesheet and script.
	 */
	public static function register_assets() {
		wp_register_style( 'dominion-radio', DOMINION_RADIO_URL . 'assets/css/dominion-radio.css', array(), self::asset_version( 'assets/css/dominion-radio.css' ) );
		wp_register_script(
			'dominion-radio',
			DOMINION_RADIO_URL . 'assets/js/dominion-radio.js',
			array(),
			self::asset_version( 'assets/js/dominion-radio.js' ),
			array(
				'in_footer' => true,
				'strategy'  => 'defer',
			)
		);
	}

	/**
	 * Load the CSS in <head> when the current page uses the shortcode (avoids a flash of unstyled content).
	 * Also checks Elementor data, because Elementor keeps shortcode widgets outside post_content.
	 */
	public static function maybe_enqueue_early() {
		if ( ! is_singular() ) {
			return;
		}
		$post = get_queried_object();
		if ( ! $post instanceof WP_Post ) {
			return;
		}
		$found = has_shortcode( $post->post_content, self::TAG );
		if ( ! $found ) {
			$elementor = get_post_meta( $post->ID, '_elementor_data', true );
			$found     = is_string( $elementor ) && false !== strpos( $elementor, self::TAG );
		}
		if ( apply_filters( 'dominion_radio_force_assets', $found, $post ) ) {
			wp_enqueue_style( 'dominion-radio' );
		}
	}

	/**
	 * Shortcode callback.
	 *
	 * @param array|string $atts Shortcode attributes.
	 * @return string
	 */
	public static function render( $atts = array() ) {
		$opts = Dominion_Radio_Settings::get();
		$atts = shortcode_atts(
			array(
				'backend'         => '',
				'name'            => $opts['station_name'],
				'tagline'         => $opts['tagline'],
				'offline_message' => $opts['offline_message'],
				'heading'         => 'h2',
			),
			is_array( $atts ) ? $atts : array(),
			self::TAG
		);

		$backend = '' !== $atts['backend'] ? Dominion_Radio_Settings::sanitize_backend_url( $atts['backend'] ) : $opts['backend_url'];
		$heading = in_array( strtolower( $atts['heading'] ), array( 'h1', 'h2', 'h3' ), true ) ? strtolower( $atts['heading'] ) : 'h2';

		// Portrait: the chosen attachment, otherwise the bundled Apostle portrait.
		// The bundled portrait is served as WebP with a JPEG fallback, keeping its
		// natural 2:3 proportions. Nothing is stretched.
		$portrait = array(
			'src'     => DOMINION_RADIO_URL . 'assets/img/portrait.webp',
			'fallback'=> DOMINION_RADIO_URL . 'assets/img/portrait.jpg',
			'width'   => 1024,
			'height'  => 1536,
			'srcset'  => '',
			'sizes'   => '',
		);
		$pid = (int) $opts['portrait_id'];
		if ( $pid && wp_attachment_is_image( $pid ) ) {
			$src = wp_get_attachment_image_src( $pid, 'full' );
			if ( $src ) {
				$portrait['src']    = $src[0];
				$portrait['fallback']= '';
				$portrait['width']  = (int) $src[1];
				$portrait['height'] = (int) $src[2];
				$portrait['srcset'] = (string) wp_get_attachment_image_srcset( $pid, 'full' );
			}
		}

		// The shortcode can run before wp_enqueue_scripts has fired (some plugins render content early).
		if ( ! wp_style_is( 'dominion-radio', 'registered' ) ) {
			self::register_assets();
		}
		wp_enqueue_style( 'dominion-radio' );
		wp_enqueue_script( 'dominion-radio' );

		++self::$count;
		$instance_id = 'dr-radio-' . self::$count;
		$can_manage  = current_user_can( 'manage_options' );

		ob_start();
		include DOMINION_RADIO_DIR . 'templates/radio-player.php';
		return (string) ob_get_clean();
	}
}
