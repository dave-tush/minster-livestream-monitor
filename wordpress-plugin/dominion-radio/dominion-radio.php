<?php
/**
 * Plugin Name: Dominion Radio
 * Description: A continuous, 24/7 audio radio station powered by the Dominion Radio Python backend. Use the [dominion_radio] shortcode to embed the player.
 * Version: 1.0.0
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 * Text Domain: dominion-radio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'DOMINION_RADIO_VERSION', '1.0.0' );
define( 'DOMINION_RADIO_URL', plugin_dir_url( __FILE__ ) );
define( 'DOMINION_RADIO_PATH', plugin_dir_path( __FILE__ ) );

// Require class files.
require_once DOMINION_RADIO_PATH . 'includes/class-settings.php';
require_once DOMINION_RADIO_PATH . 'includes/class-shortcode.php';

final class Dominion_Radio {

	const OPT_BACKEND_URL = 'dominion_radio_backend_url';
	const OPT_STATION_NAME = 'dominion_radio_station_name';
	const STATION_NAME_DEFAULT = 'Dominion Radio';

	/* ------------------------------------------------------------------
	 * Setup
	 * ---------------------------------------------------------------- */

	public static function init() {
		// Register shortcode.
		add_shortcode( 'dominion_radio', array( Dominion_Radio_Shortcode::class, 'render' ) );

		// Admin settings.
		if ( is_admin() ) {
			add_action( 'admin_menu', array( __CLASS__, 'admin_menu' ) );
			add_action( 'admin_init', array( Dominion_Radio_Settings::class, 'register_settings' ) );
		}
	}

	public static function activate() {
		// Ensure defaults exist on activation.
		Dominion_Radio_Settings::ensure_defaults();
	}

	public static function deactivate() {
		// Nothing to clean up; options are preserved on deactivation.
	}

	public static function uninstall() {
		delete_option( self::OPT_BACKEND_URL );
		delete_option( self::OPT_STATION_NAME );
	}

	/* ------------------------------------------------------------------
	 * Helpers
	 * ---------------------------------------------------------------- */

	public static function backend_url() {
		$url = trim( (string) get_option( self::OPT_BACKEND_URL, '' ) );
		if ( '' === $url ) {
			return '';
		}
		return rtrim( $url, '/' );
	}

	public static function station_name() {
		$name = trim( (string) get_option( self::OPT_STATION_NAME, '' ) );
		return '' !== $name ? $name : self::STATION_NAME_DEFAULT;
	}

	public static function admin_menu() {
		add_options_page(
			'Dominion Radio',
			'Dominion Radio',
			'manage_options',
			'dominion-radio',
			array( __CLASS__, 'render_admin' )
		);
	}

	public static function render_admin() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}

		$backend_url = self::backend_url();
		$station_name = self::station_name();
		$site = untrailingslashit( home_url() );
		$is_ssl = is_ssl();

		$notice = isset( $_GET['dr_notice'] ) ? sanitize_key( wp_unslash( $_GET['dr_notice'] ) ) : '';
		$msgs   = array(
			'saved'      => array( 'success', 'Settings saved.' ),
			'missing_url'=> array( 'error', 'Please enter the Python backend URL.' ),
			'invalid_url'=> array( 'error', 'That does not look like a valid URL. Use https://...' ),
		);
		?>
		<div class="wrap">
			<h1>Dominion Radio</h1>

			<?php if ( isset( $msgs[ $notice ] ) ) : ?>
				<div class="notice notice-<?php echo esc_attr( $msgs[ $notice ][0] ); ?> is-dismissible"><p><?php echo esc_html( $msgs[ $notice ][1] ); ?></p></div>
			<?php endif; ?>

			<?php if ( ! $is_ssl ) : ?>
				<div class="notice notice-warning"><p><strong>This site is not using HTTPS.</strong> Cross-origin audio and metadata fetches require HTTPS on both the WordPress site and the Python backend.</p></div>
			<?php endif; ?>

			<form method="post" action="<?php echo esc_url( admin_url( 'options.php' ) ); ?>">
				<?php settings_fields( 'dominion_radio' ); ?>
				<?php do_settings_sections( 'dominion_radio' ); ?>

				<h2>1. Python Backend URL</h2>
				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><label for="dr-backend-url">Backend base URL</label></th>
						<td>
							<input type="url" id="dr-backend-url" name="dominion_radio_backend_url" class="regular-text code" value="<?php echo esc_attr( $backend_url ); ?>" placeholder="https://your-backend.onrender.com">
							<p class="description">The public base URL of the Dominion Radio Python FastAPI backend. Do not include a trailing slash. Example: <code>https://minister-livestream-monitor.onrender.com</code></p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="dr-station-name">Station name</label></th>
						<td>
							<input type="text" id="dr-station-name" name="dominion_radio_station_name" class="regular-text" value="<?php echo esc_attr( $station_name ); ?>" placeholder="Dominion Radio">
							<p class="description">Displayed in the player header and browser tab.</p>
						</td>
					</tr>
				</table>

				<?php submit_button( 'Save Changes' ); ?>
			</form>

			<h2>2. Add the player to a page</h2>
			<p>Create a new page (e.g. "Radio"), insert a Shortcode block, and paste:</p>
			<p><input type="text" id="dr-code" class="regular-text code" readonly value="[dominion_radio]"> <button type="button" class="button" data-dr-copy="dr-code">Copy</button></p>

			<h2>3. Configure CORS on the backend</h2>
			<p>The Python backend must allow cross-origin requests from this WordPress site. Ensure your backend's CORS configuration includes the origin <code><?php echo esc_html( $site ); ?></code> for GET and OPTIONS requests.</p>

			<h2>4. Backend radio must be running</h2>
			<p>The Python backend must be deployed with the Dominion Radio engine active (TELEGRAM_* environment variables configured) so that <code>/radio/now</code>, <code>/radio/playlist</code>, and <code>/radio/audio/{id}</code> are available.</p>
		</div>
		<script>
		document.querySelectorAll('[data-dr-copy]').forEach(function (btn) {
			btn.addEventListener('click', function () {
				var el = document.getElementById(btn.getAttribute('data-dr-copy'));
				el.select();
				if (navigator.clipboard) { navigator.clipboard.writeText(el.value); } else { document.execCommand('copy'); }
				var label = btn.textContent; btn.textContent = 'Copied';
				setTimeout(function () { btn.textContent = label; }, 1500);
			});
		});
		</script>
		<?php
	}
}

register_activation_hook( __FILE__, array( 'Dominion_Radio', 'activate' ) );
register_deactivation_hook( __FILE__, array( 'Dominion_Radio', 'deactivate' ) );
register_uninstall_hook( __FILE__, array( 'Dominion_Radio', 'uninstall' ) );
Dominion_Radio::init();
