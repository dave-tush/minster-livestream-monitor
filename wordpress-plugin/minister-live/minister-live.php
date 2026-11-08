<?php
/**
 * Plugin Name: Minister Live
 * Description: Shows the minister's current YouTube livestream with the [minister_live] shortcode. Updated automatically by the Minister Livestream Monitor service. Never downloads or re-streams video: it plays the original YouTube stream inside a custom-styled player.
 * Version: 1.1.0
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 * Text Domain: minister-live
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'MINISTER_LIVE_VERSION', '1.1.0' );
define( 'MINISTER_LIVE_URL', plugin_dir_url( __FILE__ ) );

final class Minister_Live {

	const REST_NS       = 'minister-live/v1';
	const OPT_STATE     = 'minister_live_state';   // last state pushed by the monitoring server
	const OPT_MANUAL    = 'minister_live_manual';  // manual broadcast set from the settings page
	const OPT_KEY       = 'minister_live_api_key'; // shared secret (unless set in wp-config.php)
	const OPT_DIAG      = 'minister_live_diag';    // timestamps shown on the settings page
	const STALE_SECONDS = 1800;                    // "live" with no update for 30 min is treated as offline

	/* ------------------------------------------------------------------
	 * Setup
	 * ---------------------------------------------------------------- */

	public static function init() {
		add_action( 'rest_api_init', array( __CLASS__, 'register_routes' ) );
		add_shortcode( 'minister_live', array( __CLASS__, 'shortcode' ) );

		if ( is_admin() ) {
			add_action( 'admin_menu', array( __CLASS__, 'admin_menu' ) );
			add_action( 'admin_enqueue_scripts', array( __CLASS__, 'admin_assets' ) );
			add_action( 'admin_post_minister_live_manual', array( __CLASS__, 'handle_manual' ) );
			add_action( 'admin_post_minister_live_clear', array( __CLASS__, 'handle_clear' ) );
			add_action( 'admin_post_minister_live_newkey', array( __CLASS__, 'handle_newkey' ) );
		}
	}

	public static function activate() {
		if ( ! self::key_is_constant() && '' === (string) get_option( self::OPT_KEY, '' ) ) {
			update_option( self::OPT_KEY, self::generate_key(), false );
		}
	}

	public static function uninstall() {
		foreach ( array( self::OPT_STATE, self::OPT_MANUAL, self::OPT_KEY, self::OPT_DIAG ) as $option ) {
			delete_option( $option );
		}
	}

	/* ------------------------------------------------------------------
	 * Helpers
	 * ---------------------------------------------------------------- */

	public static function key_is_constant() {
		return defined( 'MINISTER_LIVE_API_KEY' ) && '' !== (string) MINISTER_LIVE_API_KEY;
	}

	public static function api_key() {
		if ( self::key_is_constant() ) {
			return (string) MINISTER_LIVE_API_KEY;
		}
		return (string) get_option( self::OPT_KEY, '' );
	}

	public static function generate_key() {
		return wp_generate_password( 40, false, false );
	}

	public static function is_valid_video_id( $id ) {
		return is_string( $id ) && 1 === preg_match( '/^[A-Za-z0-9_-]{11}$/', $id );
	}

	/** Accepts a bare video ID or any common YouTube URL (watch, youtu.be, live, embed, shorts). */
	public static function extract_video_id( $input ) {
		$input = trim( (string) $input );
		if ( self::is_valid_video_id( $input ) ) {
			return $input;
		}
		$parts = wp_parse_url( $input );
		if ( empty( $parts['host'] ) ) {
			return '';
		}
		$host = strtolower( preg_replace( '/^www\./', '', $parts['host'] ) );
		$path = isset( $parts['path'] ) ? $parts['path'] : '';
		$id   = '';

		if ( 'youtu.be' === $host ) {
			$id = trim( $path, '/' );
		} elseif ( in_array( $host, array( 'youtube.com', 'm.youtube.com', 'youtube-nocookie.com' ), true ) ) {
			if ( ! empty( $parts['query'] ) ) {
				parse_str( $parts['query'], $query );
				if ( ! empty( $query['v'] ) && is_string( $query['v'] ) ) {
					$id = $query['v'];
				}
			}
			if ( '' === $id && preg_match( '#^/(?:live|embed|shorts)/([A-Za-z0-9_-]{11})#', $path, $m ) ) {
				$id = $m[1];
			}
		}
		return self::is_valid_video_id( $id ) ? $id : '';
	}

	private static function clean_text( $value ) {
		return mb_substr( sanitize_text_field( (string) $value ), 0, 200 );
	}

	private static function clean_iso( $value ) {
		$time = strtotime( (string) $value );
		return $time ? gmdate( 'c', $time ) : '';
	}

	private static function note_diag( $field ) {
		$diag           = get_option( self::OPT_DIAG, array() );
		$diag           = is_array( $diag ) ? $diag : array();
		$diag[ $field ] = time();
		update_option( self::OPT_DIAG, $diag, false );
	}

	/** Whitelisted, public-safe shape. The thumbnail is built from the validated ID, never taken from input. */
	private static function public_state( array $s, $source ) {
		$id = $s['video_id'];
		return array(
			'status'        => 'live',
			'video_id'      => $id,
			'title'         => isset( $s['title'] ) ? (string) $s['title'] : '',
			'channel_title' => isset( $s['channel_title'] ) ? (string) $s['channel_title'] : '',
			'started_at'    => isset( $s['started_at'] ) ? (string) $s['started_at'] : '',
			'youtube_url'   => 'https://www.youtube.com/watch?v=' . $id,
			'thumbnail_url' => 'https://i.ytimg.com/vi/' . $id . '/hqdefault.jpg',
			'source'        => $source,
		);
	}

	public static function manual_is_active() {
		$m = get_option( self::OPT_MANUAL );
		return is_array( $m ) && ! empty( $m['video_id'] ) && isset( $m['expires_at'] ) && time() < (int) $m['expires_at'];
	}

	/** What the website should show right now: manual broadcast > fresh automatic state > offline. */
	public static function effective_state() {
		if ( self::manual_is_active() ) {
			$m = get_option( self::OPT_MANUAL );
			return self::public_state( $m, 'manual' );
		}
		$s = get_option( self::OPT_STATE );
		if ( is_array( $s ) && isset( $s['status'] ) && 'live' === $s['status'] && ! empty( $s['video_id'] ) && self::is_valid_video_id( $s['video_id'] ) ) {
			$age   = time() - (int) ( isset( $s['updated_at'] ) ? $s['updated_at'] : 0 );
			$limit = (int) apply_filters( 'minister_live_stale_seconds', self::STALE_SECONDS );
			if ( $age <= $limit ) {
				return self::public_state( $s, 'auto' );
			}
		}
		return array( 'status' => 'offline', 'source' => 'auto' );
	}

	/* ------------------------------------------------------------------
	 * REST API:  /wp-json/minister-live/v1/status
	 * ---------------------------------------------------------------- */

	public static function register_routes() {
		register_rest_route(
			self::REST_NS,
			'/status',
			array(
				array(
					'methods'             => WP_REST_Server::READABLE,
					'callback'            => array( __CLASS__, 'rest_get' ),
					'permission_callback' => '__return_true', // public: contains only what the page already shows
				),
				array(
					'methods'             => WP_REST_Server::CREATABLE,
					'callback'            => array( __CLASS__, 'rest_post' ),
					'permission_callback' => array( __CLASS__, 'rest_auth' ), // secret key required
				),
			)
		);
	}

	public static function rest_auth( $request ) {
		$key = self::api_key();
		if ( '' === $key ) {
			return new WP_Error( 'minister_live_no_key', 'No API key is configured on this site.', array( 'status' => 503 ) );
		}
		$sent = (string) $request->get_header( 'X-Minister-Live-Key' );
		if ( '' === $sent || ! hash_equals( $key, $sent ) ) {
			self::note_diag( 'last_rejected_at' );
			return new WP_Error( 'minister_live_forbidden', 'Invalid API key.', array( 'status' => 401 ) );
		}
		return true;
	}

	public static function rest_get() {
		$response = rest_ensure_response( self::effective_state() );
		$response->header( 'Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0' );
		return $response;
	}

	public static function rest_post( $request ) {
		$p = $request->get_json_params();
		if ( ! is_array( $p ) ) {
			return new WP_Error( 'minister_live_bad_body', 'Expected a JSON body.', array( 'status' => 400 ) );
		}
		$status = isset( $p['status'] ) ? $p['status'] : '';

		if ( 'offline' === $status ) {
			update_option( self::OPT_STATE, array( 'status' => 'offline', 'updated_at' => time() ), false );
			self::note_diag( 'last_push_at' );
			return rest_ensure_response( array( 'success' => true, 'status' => 'offline' ) );
		}
		if ( 'live' !== $status ) {
			return new WP_Error( 'minister_live_bad_status', 'status must be "live" or "offline".', array( 'status' => 400 ) );
		}

		$video_id = isset( $p['video_id'] ) && is_string( $p['video_id'] ) ? $p['video_id'] : '';
		if ( ! self::is_valid_video_id( $video_id ) ) {
			return new WP_Error( 'minister_live_bad_video', 'video_id must be a valid 11-character YouTube video ID.', array( 'status' => 400 ) );
		}
		$channel_id = isset( $p['channel_id'] ) ? (string) $p['channel_id'] : '';

		update_option(
			self::OPT_STATE,
			array(
				'status'        => 'live',
				'video_id'      => $video_id,
				'title'         => self::clean_text( isset( $p['title'] ) ? $p['title'] : '' ),
				'channel_title' => self::clean_text( isset( $p['channel_title'] ) ? $p['channel_title'] : '' ),
				'channel_id'    => 1 === preg_match( '/^UC[A-Za-z0-9_-]{22}$/', $channel_id ) ? $channel_id : '',
				'started_at'    => self::clean_iso( isset( $p['started_at'] ) ? $p['started_at'] : '' ),
				'updated_at'    => time(),
			),
			false
		);
		self::note_diag( 'last_push_at' );
		return rest_ensure_response( array( 'success' => true, 'status' => 'live' ) );
	}

	/* ------------------------------------------------------------------
	 * Shortcode:  [minister_live]
	 * Attributes: interval="30" (seconds, 15-300)  autoplay="no|yes"  name="..."  label="Itinerary"
	 * notify="yes|no" (show the go-live alerts switch)  offline_message="..."
	 * ---------------------------------------------------------------- */

	public static function enqueue_assets() {
		if ( apply_filters( 'minister_live_load_fonts', true ) ) {
			// Same handle and URL as the Sermon Playlist Player, so WordPress loads the fonts only once.
			wp_enqueue_style( 'sp-fonts', 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@500;600;700&display=swap', array(), null );
		}
		wp_enqueue_style( 'minister-live', MINISTER_LIVE_URL . 'assets/minister-live.css', array(), MINISTER_LIVE_VERSION );
		wp_enqueue_script( 'minister-live', MINISTER_LIVE_URL . 'assets/minister-live.js', array(), MINISTER_LIVE_VERSION, true );
	}

	public static function shortcode( $atts ) {
		$atts = shortcode_atts(
			array(
				'interval'        => '30',
				'autoplay'        => 'no',
				'name'            => 'Apostle Toluwalogo Agboola',
				'label'           => 'Itinerary',
				'notify'          => 'yes',
				'offline_message' => '',
			),
			$atts,
			'minister_live'
		);
		self::enqueue_assets();

		$interval = max( 15, min( 300, (int) $atts['interval'] ) );
		$autoplay = in_array( strtolower( (string) $atts['autoplay'] ), array( 'yes', '1', 'true' ), true );
		$notify   = ! in_array( strtolower( (string) $atts['notify'] ), array( 'no', '0', 'false' ), true );
		$name     = '' !== trim( (string) $atts['name'] ) ? (string) $atts['name'] : 'The minister';
		$offline  = '' !== trim( (string) $atts['offline_message'] )
			? (string) $atts['offline_message']
			/* translators: %s: the minister's name */
			: sprintf( __( '%s is not live right now.', 'minister-live' ), $name );
		$state    = self::effective_state();
		$live     = 'live' === $state['status'];
		$title    = $live ? $state['title'] : '';
		$thumb    = $live ? $state['thumbnail_url'] : '';

		ob_start();
		?>
		<div class="ml-live"
			data-endpoint="<?php echo esc_url( rest_url( self::REST_NS . '/status' ) ); ?>"
			data-interval="<?php echo esc_attr( $interval ); ?>"
			data-autoplay="<?php echo $autoplay ? '1' : '0'; ?>"
			data-name="<?php echo esc_attr( $name ); ?>"
			data-video-id="<?php echo esc_attr( $live ? $state['video_id'] : '' ); ?>">

			<div class="ml-live__head">
				<div class="ml-live__head-text">
					<?php if ( '' !== trim( (string) $atts['label'] ) ) : ?>
						<span class="ml-live__eyebrow"><?php echo esc_html( $atts['label'] ); ?></span>
					<?php endif; ?>
					<div class="ml-live__name" role="heading" aria-level="2"><?php echo esc_html( $name ); ?></div>
				</div>
				<span class="ml-live__status<?php echo $live ? ' is-live' : ''; ?>" role="status" aria-live="polite">
					<span class="ml-live__dot" aria-hidden="true"></span>
					<span class="ml-live__status-text"><?php echo $live ? 'LIVE NOW' : 'NOT LIVE'; ?></span>
				</span>
			</div>

			<div class="ml-live__offline"<?php echo $live ? ' hidden' : ''; ?>>
				<svg class="ml-live__idle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/></svg>
				<p class="ml-live__offline-text"><?php echo esc_html( $offline ); ?></p>
				<?php if ( $notify ) : ?>
					<p class="ml-live__offline-sub"><?php esc_html_e( 'Check back soon, or switch on alerts below and we will tell you the moment the broadcast begins.', 'minister-live' ); ?></p>
				<?php endif; ?>
			</div>

			<div class="ml-live__player"<?php echo $live ? '' : ' hidden'; ?>>
				<div class="ml-live__stage">
					<div class="ml-live__frame is-loading">
						<div class="ml-live__yt"></div>
						<div class="ml-live__poster"<?php echo $live ? ' style="background-image:url(\'' . esc_url( $thumb ) . '\')"' : ''; ?>>
							<span class="ml-live__big" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 4.6v14.8a1 1 0 0 0 1.52.85l12-7.4a1 1 0 0 0 0-1.7l-12-7.4A1 1 0 0 0 7 4.6z"/></svg></span>
						</div>
						<span class="ml-live__spin" aria-hidden="true"></span>
						<button type="button" class="ml-live__shield" aria-label="<?php esc_attr_e( 'Play or pause the broadcast', 'minister-live' ); ?>"></button>
						<div class="ml-live__controls">
							<button type="button" class="ml-live__btn ml-live__btn--main" aria-label="Play">
								<svg class="ml-live__i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.6v14.8a1 1 0 0 0 1.52.85l12-7.4a1 1 0 0 0 0-1.7l-12-7.4A1 1 0 0 0 7 4.6z"/></svg>
								<svg class="ml-live__i-pause" viewBox="0 0 24 24" aria-hidden="true" hidden><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>
							</button>
							<span class="ml-live__tag"><i aria-hidden="true"></i>LIVE</span>
							<span class="ml-live__spacer"></span>
							<button type="button" class="ml-live__btn ml-live__mute" aria-label="Mute">
								<svg class="ml-live__i-vol is-stroke" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/></svg>
								<svg class="ml-live__i-muted is-stroke" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg>
							</button>
							<button type="button" class="ml-live__btn ml-live__fs" aria-label="Full screen">
								<svg class="is-stroke" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>
							</button>
						</div>
					</div>
					<div class="ml-live__meta">
						<p class="ml-live__title"<?php echo '' !== $title ? '' : ' hidden'; ?>><?php echo esc_html( $title ); ?></p>
					</div>
				</div>
			</div>

			<?php if ( $notify ) : ?>
				<div class="ml-live__notify">
					<svg class="ml-live__notify-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20.5a2 2 0 0 0 4 0"/></svg>
					<div class="ml-live__notify-text">
						<span class="ml-live__notify-title"><?php esc_html_e( 'Go-live alerts', 'minister-live' ); ?></span>
						<span class="ml-live__notify-sub"><?php esc_html_e( 'Switch on to be alerted when the broadcast begins.', 'minister-live' ); ?></span>
					</div>
					<button type="button" class="ml-live__switch" role="switch" aria-checked="false" aria-label="<?php esc_attr_e( 'Go-live alerts', 'minister-live' ); ?>"></button>
				</div>
			<?php endif; ?>
		</div>
		<?php
		return ob_get_clean();
	}

	/* ------------------------------------------------------------------
	 * Admin: Settings > Minister Live
	 * ---------------------------------------------------------------- */

	public static function admin_menu() {
		add_options_page( 'Minister Live', 'Minister Live', 'manage_options', 'minister-live', array( __CLASS__, 'render_admin' ) );
	}

	public static function admin_assets( $hook ) {
		if ( 'settings_page_minister-live' === $hook ) {
			self::enqueue_assets(); // for the live preview
		}
	}

	private static function admin_guard( $action ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( 'You are not allowed to do this.', 403 );
		}
		check_admin_referer( $action );
	}

	private static function back( $code ) {
		wp_safe_redirect( add_query_arg( 'ml_notice', $code, admin_url( 'options-general.php?page=minister-live' ) ) );
		exit;
	}

	public static function handle_manual() {
		self::admin_guard( 'minister_live_manual' );
		$video = isset( $_POST['video'] ) ? self::extract_video_id( wp_unslash( $_POST['video'] ) ) : '';
		if ( '' === $video ) {
			self::back( 'bad_video' );
		}
		$hours = isset( $_POST['hours'] ) ? max( 1, min( 48, (int) $_POST['hours'] ) ) : 3;
		update_option(
			self::OPT_MANUAL,
			array(
				'video_id'      => $video,
				'title'         => self::clean_text( isset( $_POST['title'] ) ? wp_unslash( $_POST['title'] ) : '' ),
				'channel_title' => self::clean_text( isset( $_POST['channel_title'] ) ? wp_unslash( $_POST['channel_title'] ) : '' ),
				'created_at'    => time(),
				'expires_at'    => time() + $hours * HOUR_IN_SECONDS,
			),
			false
		);
		self::back( 'manual_set' );
	}

	public static function handle_clear() {
		self::admin_guard( 'minister_live_clear' );
		delete_option( self::OPT_MANUAL );
		self::back( 'manual_cleared' );
	}

	public static function handle_newkey() {
		self::admin_guard( 'minister_live_newkey' );
		if ( ! self::key_is_constant() ) {
			update_option( self::OPT_KEY, self::generate_key(), false );
		}
		self::back( 'key_regenerated' );
	}

	public static function render_admin() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		if ( ! self::key_is_constant() && '' === (string) get_option( self::OPT_KEY, '' ) ) {
			update_option( self::OPT_KEY, self::generate_key(), false );
		}

		$const    = self::key_is_constant();
		$state    = self::effective_state();
		$stored   = get_option( self::OPT_STATE );
		$diag     = get_option( self::OPT_DIAG, array() );
		$diag     = is_array( $diag ) ? $diag : array();
		$manual   = self::manual_is_active() ? get_option( self::OPT_MANUAL ) : null;
		$site     = untrailingslashit( home_url() );
		$endpoint = rest_url( self::REST_NS . '/status' );
		$ago      = function ( $field ) use ( $diag ) {
			return empty( $diag[ $field ] ) ? 'never' : human_time_diff( (int) $diag[ $field ], time() ) . ' ago';
		};
		$stale = ! $manual && is_array( $stored ) && isset( $stored['status'] ) && 'live' === $stored['status'] && 'live' !== $state['status'];

		$notice = isset( $_GET['ml_notice'] ) ? sanitize_key( wp_unslash( $_GET['ml_notice'] ) ) : ''; // phpcs:ignore WordPress.Security.NonceVerification
		$msgs   = array(
			'manual_set'      => array( 'success', 'Manual broadcast is now showing on the site.' ),
			'manual_cleared'  => array( 'success', 'Manual broadcast cleared. The site now follows the automatic monitor again.' ),
			'bad_video'       => array( 'error', 'That does not look like a YouTube link or video ID. Paste a link such as https://www.youtube.com/watch?v=XXXXXXXXXXX.' ),
			'key_regenerated' => array( 'warning', 'New API key generated. Send it to the site owner: the old key no longer works.' ),
		);
		?>
		<div class="wrap">
			<h1>Minister Live</h1>

			<?php if ( isset( $msgs[ $notice ] ) ) : ?>
				<div class="notice notice-<?php echo esc_attr( $msgs[ $notice ][0] ); ?> is-dismissible"><p><?php echo esc_html( $msgs[ $notice ][1] ); ?></p></div>
			<?php endif; ?>
			<?php if ( ! is_ssl() ) : ?>
				<div class="notice notice-warning"><p><strong>This site is not using HTTPS.</strong> The monitoring server only talks to https:// addresses. Enable HTTPS before connecting it.</p></div>
			<?php endif; ?>

			<h2>1. Connection details <small style="font-weight:400">(send these two values to the site owner)</small></h2>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="ml-site">Site address</label></th>
					<td>
						<input type="text" id="ml-site" class="regular-text code" readonly value="<?php echo esc_attr( $site ); ?>">
						<button type="button" class="button" data-ml-copy="ml-site">Copy</button>
						<p class="description">The owner enters this as <code>WORDPRESS_URL</code>. It must be the exact final address (check <code>www</code> and <code>https</code>).</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="ml-key">API key</label></th>
					<td>
						<?php if ( $const ) : ?>
							<p>The key is set in <code>wp-config.php</code> (<code>MINISTER_LIVE_API_KEY</code>) and is not shown here.</p>
						<?php else : ?>
							<input type="text" id="ml-key" class="large-text code" readonly value="<?php echo esc_attr( self::api_key() ); ?>">
							<button type="button" class="button" data-ml-copy="ml-key">Copy</button>
							<p class="description">The owner enters this as <code>WORDPRESS_API_KEY</code>. Treat it like a password: share it through a password manager, not plain chat or email.</p>
							<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" style="margin-top:8px">
								<input type="hidden" name="action" value="minister_live_newkey">
								<?php wp_nonce_field( 'minister_live_newkey' ); ?>
								<button type="submit" class="button button-link-delete" onclick="return confirm('Generate a new key? The monitoring server will stop working until it is given the new one.');">Generate a new key</button>
							</form>
						<?php endif; ?>
					</td>
				</tr>
				<tr>
					<th scope="row">Endpoint</th>
					<td><code><?php echo esc_html( $endpoint ); ?></code></td>
				</tr>
			</table>

			<h2>2. Status</h2>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row">Website is showing</th>
					<td>
						<?php if ( 'live' === $state['status'] ) : ?>
							<strong style="color:#b32d2e">&#9679; LIVE</strong>
							&ndash; <?php echo esc_html( '' !== $state['title'] ? $state['title'] : $state['video_id'] ); ?>
							<em>(<?php echo 'manual' === $state['source'] ? 'manual broadcast' : 'from the monitoring server'; ?>)</em>
						<?php else : ?>
							<strong>Offline</strong> &ndash; visitors see that the minister is not live
						<?php endif; ?>
						<?php if ( $stale ) : ?>
							<p class="description">The last update said "live" but nothing has been received for over 30 minutes, so the site safely shows Offline.</p>
						<?php endif; ?>
					</td>
				</tr>
				<tr><th scope="row">Last update from server</th><td><?php echo esc_html( $ago( 'last_push_at' ) ); ?>
					<p class="description">The monitoring server sends an update every few minutes, even when nothing changes. "never" means it has not connected yet.</p></td></tr>
				<tr><th scope="row">Last rejected request</th><td><?php echo esc_html( $ago( 'last_rejected_at' ) ); ?>
					<p class="description">A recent time here means something is calling the endpoint with the wrong API key.</p></td></tr>
			</table>

			<h2>3. Add it to a page</h2>
			<p>Paste this shortcode into a page, post or widget (use the "Shortcode" block in the block editor, or the Shortcode widget in Elementor):</p>
			<p><input type="text" id="ml-code" class="regular-text code" readonly value="[minister_live]"> <button type="button" class="button" data-ml-copy="ml-code">Copy</button></p>
			<p class="description">Options: <code>interval="30"</code> (seconds between checks, 15&ndash;300), <code>autoplay="yes"</code> (starts muted), <code>name="Apostle Toluwalogo Agboola"</code>, <code>label="Itinerary"</code>, <code>notify="no"</code> (hides the alerts switch), <code>offline_message="Back on Sunday"</code>.</p>

			<h2>4. Manual broadcast <small style="font-weight:400">(testing, or guest appearances the monitor cannot detect)</small></h2>
			<p>Shows any YouTube video on the site immediately. It overrides the automatic monitor until it expires or you clear it. The video does not need to be live, so this is also the quickest way to test the display.</p>
			<?php if ( $manual ) : ?>
				<p><strong>A manual broadcast is active until <?php echo esc_html( wp_date( 'M j, g:i a', (int) $manual['expires_at'] ) ); ?>.</strong></p>
				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="minister_live_clear">
					<?php wp_nonce_field( 'minister_live_clear' ); ?>
					<?php submit_button( 'Clear manual broadcast', 'secondary', 'submit', false ); ?>
				</form>
				<p>&nbsp;</p>
			<?php endif; ?>
			<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
				<input type="hidden" name="action" value="minister_live_manual">
				<?php wp_nonce_field( 'minister_live_manual' ); ?>
				<table class="form-table" role="presentation">
					<tr><th scope="row"><label for="ml-video">YouTube link or video ID</label></th><td><input type="text" id="ml-video" name="video" class="regular-text" required placeholder="https://www.youtube.com/watch?v=..."></td></tr>
					<tr><th scope="row"><label for="ml-title">Title (optional)</label></th><td><input type="text" id="ml-title" name="title" class="regular-text" maxlength="200"></td></tr>
					<tr><th scope="row"><label for="ml-channel">Channel name (optional)</label></th><td><input type="text" id="ml-channel" name="channel_title" class="regular-text" maxlength="200"></td></tr>
					<tr><th scope="row"><label for="ml-hours">Keep showing for</label></th><td>
						<select id="ml-hours" name="hours">
							<?php foreach ( array( 1, 3, 6, 12, 24, 48 ) as $h ) : ?>
								<option value="<?php echo esc_attr( $h ); ?>"<?php selected( 3, $h ); ?>><?php echo esc_html( $h . ( 1 === $h ? ' hour' : ' hours' ) ); ?></option>
							<?php endforeach; ?>
						</select></td></tr>
				</table>
				<?php submit_button( 'Go live now', 'primary', 'submit', false ); ?>
			</form>

			<h2>5. Preview</h2>
			<p>This is exactly what visitors see (it updates by itself):</p>
			<div style="max-width:720px"><?php echo do_shortcode( '[minister_live]' ); // phpcs:ignore WordPress.Security.EscapeOutput ?></div>
		</div>
		<script>
		document.querySelectorAll('[data-ml-copy]').forEach(function (btn) {
			btn.addEventListener('click', function () {
				var el = document.getElementById(btn.getAttribute('data-ml-copy'));
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

register_activation_hook( __FILE__, array( 'Minister_Live', 'activate' ) );
register_uninstall_hook( __FILE__, array( 'Minister_Live', 'uninstall' ) );
Minister_Live::init();
