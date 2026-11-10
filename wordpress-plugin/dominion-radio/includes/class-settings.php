<?php
/**
 * Settings page, backend URL validation and the administrator-only backend actions.
 *
 * The backend ADMIN_API_KEY (needed only for POST /radio/sync-now) is used exclusively on the
 * server, from this class. It is never printed into a page, script or data attribute.
 *
 * @package DominionRadio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Dominion_Radio_Settings {

	const OPTION = 'dominion_radio_options';
	const SLUG   = 'dominion-radio';

	/**
	 * Default option values.
	 *
	 * @return array
	 */
	public static function defaults() {
		return array(
			'backend_url'     => '',
			'station_name'    => 'Dominion Radio',
			'tagline'         => 'A continuous stream of sermons, worship and messages to strengthen your walk with God.',
			'offline_message' => 'Dominion Radio is not on air right now.',
			'portrait_id'     => 0,
			'admin_api_key'   => '',
		);
	}

	/**
	 * Read one option (or all of them).
	 *
	 * @param string|null $key Option key.
	 * @return mixed
	 */
	public static function get( $key = null ) {
		$saved = get_option( self::OPTION, array() );
		$opts  = wp_parse_args( is_array( $saved ) ? $saved : array(), self::defaults() );
		// Nothing saved yet: fall back to the bundled production backend so the player works out of the box.
		if ( '' === $opts['backend_url'] ) {
			$opts['backend_url'] = self::sanitize_backend_url( (string) apply_filters( 'dominion_radio_default_backend', DOMINION_RADIO_DEFAULT_BACKEND ) );
		}
		if ( null === $key ) {
			return $opts;
		}
		return isset( $opts[ $key ] ) ? $opts[ $key ] : '';
	}

	/**
	 * The backend ADMIN_API_KEY. A DOMINION_RADIO_ADMIN_KEY constant in wp-config.php wins over the saved option.
	 *
	 * @return string
	 */
	public static function admin_key() {
		if ( defined( 'DOMINION_RADIO_ADMIN_KEY' ) && is_string( DOMINION_RADIO_ADMIN_KEY ) && '' !== DOMINION_RADIO_ADMIN_KEY ) {
			return DOMINION_RADIO_ADMIN_KEY;
		}
		return (string) self::get( 'admin_api_key' );
	}

	/**
	 * Validate and normalise a backend base URL.
	 *
	 * Accepts https://host[:port][/base-path]. Plain http is accepted only for localhost
	 * (development), because a secure site cannot load audio from an insecure origin.
	 *
	 * @param string $url Raw URL.
	 * @return string Normalised URL without trailing slash, or '' if invalid.
	 */
	public static function sanitize_backend_url( $url ) {
		$url = trim( (string) $url );
		if ( '' === $url || strlen( $url ) > 300 ) {
			return '';
		}
		$parts = wp_parse_url( $url );
		if ( ! is_array( $parts ) || empty( $parts['scheme'] ) || empty( $parts['host'] ) ) {
			return '';
		}
		$scheme = strtolower( $parts['scheme'] );
		$host   = strtolower( $parts['host'] );
		if ( isset( $parts['user'] ) || isset( $parts['pass'] ) || isset( $parts['query'] ) || isset( $parts['fragment'] ) ) {
			return '';
		}
		$is_local = in_array( $host, array( 'localhost', '127.0.0.1', '[::1]', '::1' ), true );
		if ( 'https' !== $scheme && ! ( 'http' === $scheme && $is_local ) ) {
			return '';
		}
		$clean = $scheme . '://' . $host;
		if ( ! empty( $parts['port'] ) ) {
			$clean .= ':' . (int) $parts['port'];
		}
		if ( ! empty( $parts['path'] ) ) {
			$clean .= '/' . trim( $parts['path'], '/' );
		}
		return esc_url_raw( rtrim( $clean, '/' ) );
	}

	/**
	 * Hook everything up.
	 */
	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'add_menu' ) );
		add_action( 'admin_init', array( __CLASS__, 'register' ) );
		add_action( 'admin_init', array( 'Dominion_Radio_Page', 'maybe_create_page' ) );
		add_action( 'admin_post_dominion_radio_test', array( __CLASS__, 'handle_test' ) );
		add_action( 'admin_post_dominion_radio_sync', array( __CLASS__, 'handle_sync' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( DOMINION_RADIO_FILE ), array( __CLASS__, 'action_links' ) );
	}

	/**
	 * "Settings" link on the Plugins screen.
	 *
	 * @param array $links Existing links.
	 * @return array
	 */
	public static function action_links( $links ) {
		$url = admin_url( 'options-general.php?page=' . self::SLUG );
		array_unshift( $links, '<a href="' . esc_url( $url ) . '">' . esc_html__( 'Settings', 'dominion-radio' ) . '</a>' );
		return $links;
	}

	/**
	 * Settings > Dominion Radio.
	 */
	public static function add_menu() {
		add_options_page(
			__( 'Dominion Radio', 'dominion-radio' ),
			__( 'Dominion Radio', 'dominion-radio' ),
			'manage_options',
			self::SLUG,
			array( __CLASS__, 'render_page' )
		);
	}

	/**
	 * Register the option (Settings API handles the nonce and capability check on save).
	 */
	public static function register() {
		register_setting(
			'dominion_radio',
			self::OPTION,
			array(
				'type'              => 'array',
				'sanitize_callback' => array( __CLASS__, 'sanitize' ),
				'default'           => self::defaults(),
			)
		);
	}

	/**
	 * Sanitise the whole options array.
	 *
	 * @param mixed $input Raw form input.
	 * @return array
	 */
	public static function sanitize( $input ) {
		$old   = self::get();
		$input = is_array( $input ) ? $input : array();
		$out   = $old;

		if ( isset( $input['backend_url'] ) ) {
			$raw = trim( wp_unslash( (string) $input['backend_url'] ) );
			if ( '' === $raw ) {
				$out['backend_url'] = '';
			} else {
				$clean = self::sanitize_backend_url( $raw );
				if ( '' === $clean ) {
					// add_settings_error() only exists inside wp-admin; this callback can also run via REST or WP-CLI.
					if ( function_exists( 'add_settings_error' ) ) {
						add_settings_error( self::OPTION, 'dr_url', __( 'The backend URL was not saved. Use a full https:// address such as https://your-radio.onrender.com (no query string, no credentials).', 'dominion-radio' ) );
					}
				} else {
					$out['backend_url'] = $clean;
				}
			}
		}
		if ( isset( $input['station_name'] ) ) {
			$name                = sanitize_text_field( wp_unslash( $input['station_name'] ) );
			$out['station_name'] = '' !== $name ? mb_substr( $name, 0, 60 ) : self::defaults()['station_name'];
		}
		if ( isset( $input['tagline'] ) ) {
			$out['tagline'] = mb_substr( sanitize_text_field( wp_unslash( $input['tagline'] ) ), 0, 160 );
		}
		if ( isset( $input['offline_message'] ) ) {
			$msg                    = sanitize_text_field( wp_unslash( $input['offline_message'] ) );
			$out['offline_message'] = '' !== $msg ? mb_substr( $msg, 0, 160 ) : self::defaults()['offline_message'];
		}
		if ( isset( $input['portrait_id'] ) ) {
			$id = absint( $input['portrait_id'] );
			// Only accept a real image attachment.
			$out['portrait_id'] = ( $id && wp_attachment_is_image( $id ) ) ? $id : 0;
		}
		// Admin key: blank input keeps the saved key; the "remove" box clears it.
		if ( ! empty( $input['remove_admin_api_key'] ) ) {
			$out['admin_api_key'] = '';
		} elseif ( isset( $input['admin_api_key'] ) && '' !== trim( (string) $input['admin_api_key'] ) ) {
			$key                  = preg_replace( '/[^\x21-\x7E]/', '', wp_unslash( (string) $input['admin_api_key'] ) );
			$out['admin_api_key'] = substr( (string) $key, 0, 200 );
		}
		unset( $out['remove_admin_api_key'] );
		return $out;
	}

	/**
	 * Server-side request to the backend.
	 *
	 * @param string $method  GET or POST.
	 * @param string $path    Path beginning with /.
	 * @param array  $headers Request headers.
	 * @param int    $timeout Seconds.
	 * @return array|WP_Error
	 */
	private static function backend_request( $method, $path, $headers = array(), $timeout = 15 ) {
		$base = self::get( 'backend_url' );
		if ( '' === $base ) {
			return new WP_Error( 'dr_no_backend', __( 'Save a backend URL first.', 'dominion-radio' ) );
		}
		$args = array(
			'timeout'     => $timeout,
			'redirection' => 0,
			'headers'     => $headers,
		);
		return 'POST' === $method ? wp_safe_remote_post( $base . $path, $args ) : wp_safe_remote_get( $base . $path, $args );
	}

	/**
	 * Remember a result message for the next settings page load.
	 *
	 * @param string $type    success|warning|error.
	 * @param string $message Plain text.
	 */
	private static function flash( $type, $message ) {
		set_transient( 'dominion_radio_flash_' . get_current_user_id(), array( $type, $message ), 120 );
	}

	/**
	 * Common guard for the two admin-post actions.
	 *
	 * @param string $action Nonce action.
	 */
	private static function guard( $action ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You do not have permission to do this.', 'dominion-radio' ), 403 );
		}
		check_admin_referer( $action );
	}

	/**
	 * Redirect back to the settings page.
	 */
	private static function back() {
		wp_safe_redirect( admin_url( 'options-general.php?page=' . self::SLUG ) );
		exit;
	}

	/**
	 * "Test connection": GET /radio/now and check the CORS header for this site's origin.
	 */
	public static function handle_test() {
		self::guard( 'dominion_radio_test' );

		$home   = wp_parse_url( home_url() );
		$origin = $home['scheme'] . '://' . $home['host'] . ( ! empty( $home['port'] ) ? ':' . $home['port'] : '' );
		$res    = self::backend_request( 'GET', '/radio/now', array( 'Origin' => $origin, 'Accept' => 'application/json' ) );

		if ( is_wp_error( $res ) ) {
			self::flash( 'error', sprintf( /* translators: %s: error */ __( 'Could not reach the backend: %s', 'dominion-radio' ), $res->get_error_message() ) );
			self::back();
		}
		$code = (int) wp_remote_retrieve_response_code( $res );
		$data = json_decode( wp_remote_retrieve_body( $res ), true );
		if ( 503 === $code ) {
			self::flash( 'warning', __( 'The backend is reachable but reports the station has no audio yet (HTTP 503). Run a sync, or wait for the first Telegram sync.', 'dominion-radio' ) );
			self::back();
		}
		if ( 200 !== $code || ! is_array( $data ) || empty( $data['track']['title'] ) ) {
			self::flash( 'error', sprintf( /* translators: %d: HTTP status */ __( 'Unexpected response from /radio/now (HTTP %d).', 'dominion-radio' ), $code ) );
			self::back();
		}
		$acao = wp_remote_retrieve_header( $res, 'access-control-allow-origin' );
		$cors = ( $acao === $origin || '*' === $acao )
			? sprintf( /* translators: %s: origin */ __( 'CORS OK for %s.', 'dominion-radio' ), $origin )
			: sprintf( /* translators: %s: origin */ __( 'WARNING: the backend does not allow this site (%s) in its CORS settings, so browsers will block the player.', 'dominion-radio' ), $origin );
		$msg  = sprintf(
			/* translators: 1: track title, 2: tracks in rotation */
			__( 'Connected. On air: "%1$s" (%2$d tracks in rotation). ', 'dominion-radio' ),
			sanitize_text_field( $data['track']['title'] ),
			isset( $data['tracks_in_rotation'] ) ? (int) $data['tracks_in_rotation'] : 0
		) . $cors;
		self::flash( ( $acao === $origin || '*' === $acao ) ? 'success' : 'warning', $msg );
		self::back();
	}

	/**
	 * "Sync now": POST /radio/sync-now with the server-held admin key.
	 */
	public static function handle_sync() {
		self::guard( 'dominion_radio_sync' );

		$key = self::admin_key();
		if ( '' === $key ) {
			self::flash( 'error', __( 'No admin API key is saved, so sync cannot run.', 'dominion-radio' ) );
			self::back();
		}
		$res = self::backend_request( 'POST', '/radio/sync-now', array( 'X-API-Key' => $key, 'Accept' => 'application/json' ), 60 );
		if ( is_wp_error( $res ) ) {
			self::flash( 'error', sprintf( /* translators: %s: error */ __( 'Sync request failed: %s', 'dominion-radio' ), $res->get_error_message() ) );
			self::back();
		}
		$code = (int) wp_remote_retrieve_response_code( $res );
		$data = json_decode( wp_remote_retrieve_body( $res ), true );
		if ( 200 === $code && is_array( $data ) ) {
			self::flash(
				'success',
				sprintf(
					/* translators: 1: tracks added, 2: total */
					__( 'Sync complete. %1$d new track(s) added; %2$d in rotation.', 'dominion-radio' ),
					isset( $data['added'] ) ? (int) $data['added'] : 0,
					isset( $data['tracks_in_rotation'] ) ? (int) $data['tracks_in_rotation'] : 0
				)
			);
		} elseif ( 401 === $code ) {
			self::flash( 'error', __( 'The backend rejected the admin API key (HTTP 401).', 'dominion-radio' ) );
		} elseif ( 503 === $code ) {
			self::flash( 'error', __( 'The backend has no ADMIN_API_KEY configured, so sync is disabled there (HTTP 503).', 'dominion-radio' ) );
		} else {
			self::flash( 'error', sprintf( /* translators: %d: HTTP status */ __( 'Sync failed (HTTP %d). See the backend logs.', 'dominion-radio' ), $code ) );
		}
		self::back();
	}

	/**
	 * Render the settings page.
	 */
	public static function render_page() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$o        = self::get();
		$name     = self::OPTION;
		$img_url  = $o['portrait_id'] ? wp_get_attachment_image_url( (int) $o['portrait_id'], 'medium' ) : '';
		$has_key  = '' !== self::admin_key();
		$const    = defined( 'DOMINION_RADIO_ADMIN_KEY' ) && '' !== DOMINION_RADIO_ADMIN_KEY;
		$flash_k  = 'dominion_radio_flash_' . get_current_user_id();
		$flash    = get_transient( $flash_k );
		if ( $flash ) {
			delete_transient( $flash_k );
		}
		wp_enqueue_media();
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'Dominion Radio', 'dominion-radio' ); ?></h1>
			<p><?php esc_html_e( 'Place the player on any page with the shortcode:', 'dominion-radio' ); ?> <code>[dominion_radio]</code></p>

			<?php if ( is_array( $flash ) ) : ?>
				<div class="notice notice-<?php echo esc_attr( in_array( $flash[0], array( 'success', 'warning', 'error' ), true ) ? $flash[0] : 'info' ); ?> is-dismissible"><p><?php echo esc_html( $flash[1] ); ?></p></div>
			<?php endif; ?>
			<?php settings_errors( self::OPTION ); ?>

			<form method="post" action="options.php">
				<?php settings_fields( 'dominion_radio' ); ?>
				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><label for="dr-backend"><?php esc_html_e( 'Backend URL', 'dominion-radio' ); ?></label></th>
						<td>
							<input id="dr-backend" type="url" class="regular-text code" name="<?php echo esc_attr( $name ); ?>[backend_url]" value="<?php echo esc_attr( $o['backend_url'] ); ?>" placeholder="https://your-radio.onrender.com" />
							<p class="description"><?php esc_html_e( 'The address of the Dominion Radio backend, without a trailing slash. It must be https:// and must allow this website in its CORS settings.', 'dominion-radio' ); ?></p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="dr-name"><?php esc_html_e( 'Station name', 'dominion-radio' ); ?></label></th>
						<td><input id="dr-name" type="text" class="regular-text" maxlength="60" name="<?php echo esc_attr( $name ); ?>[station_name]" value="<?php echo esc_attr( $o['station_name'] ); ?>" /></td>
					</tr>
					<tr>
						<th scope="row"><label for="dr-tagline"><?php esc_html_e( 'Tagline', 'dominion-radio' ); ?></label></th>
						<td><input id="dr-tagline" type="text" class="large-text" maxlength="160" name="<?php echo esc_attr( $name ); ?>[tagline]" value="<?php echo esc_attr( $o['tagline'] ); ?>" /></td>
					</tr>
					<tr>
						<th scope="row"><label for="dr-offline"><?php esc_html_e( 'Off-air message', 'dominion-radio' ); ?></label></th>
						<td><input id="dr-offline" type="text" class="large-text" maxlength="160" name="<?php echo esc_attr( $name ); ?>[offline_message]" value="<?php echo esc_attr( $o['offline_message'] ); ?>" /></td>
					</tr>
					<tr>
						<th scope="row"><?php esc_html_e( 'Portrait', 'dominion-radio' ); ?></th>
						<td>
							<input type="hidden" id="dr-portrait-id" name="<?php echo esc_attr( $name ); ?>[portrait_id]" value="<?php echo esc_attr( (int) $o['portrait_id'] ); ?>" />
							<div id="dr-portrait-preview" style="margin-bottom:8px"><?php if ( $img_url ) : ?><img src="<?php echo esc_url( $img_url ); ?>" alt="" style="max-width:150px;height:auto;display:block" /><?php endif; ?></div>
							<button type="button" class="button" id="dr-portrait-pick"><?php esc_html_e( 'Choose from Media Library', 'dominion-radio' ); ?></button>
							<button type="button" class="button-link" id="dr-portrait-clear"><?php esc_html_e( 'Remove', 'dominion-radio' ); ?></button>
							<p class="description"><?php esc_html_e( 'A tall, transparent or dark-background portrait of Apostle Toluwalogo Agboola works best. Its proportions are never changed. If none is chosen, a neutral placeholder silhouette is shown.', 'dominion-radio' ); ?></p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="dr-key"><?php esc_html_e( 'Backend admin API key', 'dominion-radio' ); ?></label></th>
						<td>
							<?php if ( $const ) : ?>
								<p><em><?php esc_html_e( 'Provided by the DOMINION_RADIO_ADMIN_KEY constant in wp-config.php.', 'dominion-radio' ); ?></em></p>
							<?php else : ?>
								<input id="dr-key" type="password" class="regular-text code" autocomplete="new-password" name="<?php echo esc_attr( $name ); ?>[admin_api_key]" value="" placeholder="<?php echo $has_key ? esc_attr__( '(saved — leave blank to keep)', 'dominion-radio' ) : ''; ?>" />
								<?php if ( $has_key ) : ?>
									<label style="margin-left:8px"><input type="checkbox" name="<?php echo esc_attr( $name ); ?>[remove_admin_api_key]" value="1" /> <?php esc_html_e( 'Remove saved key', 'dominion-radio' ); ?></label>
								<?php endif; ?>
							<?php endif; ?>
							<p class="description"><?php esc_html_e( 'Optional. Only used by the "Sync now" button below, from the server. It is never sent to visitors\' browsers. For better security, define DOMINION_RADIO_ADMIN_KEY in wp-config.php instead.', 'dominion-radio' ); ?></p>
						</td>
					</tr>
				</table>
				<?php submit_button(); ?>
			</form>

			<h2><?php esc_html_e( 'Backend tools', 'dominion-radio' ); ?></h2>
			<div style="display:flex;gap:12px;flex-wrap:wrap">
				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="dominion_radio_test" />
					<?php wp_nonce_field( 'dominion_radio_test' ); ?>
					<?php submit_button( __( 'Test connection', 'dominion-radio' ), 'secondary', 'submit', false, array( 'disabled' => '' === $o['backend_url'] ? 'disabled' : null ) ); ?>
				</form>
				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="dominion_radio_sync" />
					<?php wp_nonce_field( 'dominion_radio_sync' ); ?>
					<?php submit_button( __( 'Sync new uploads now', 'dominion-radio' ), 'secondary', 'submit', false, array( 'disabled' => ( '' === $o['backend_url'] || ! $has_key ) ? 'disabled' : null ) ); ?>
				</form>
			</div>
			<p class="description"><?php esc_html_e( '"Test connection" calls /radio/now and checks the CORS setting for this site. "Sync" asks the backend to pull new audio from Telegram immediately (needs the admin key).', 'dominion-radio' ); ?></p>

			<h2><?php esc_html_e( 'Radio page', 'dominion-radio' ); ?></h2>
			<?php
			$page_id = Dominion_Radio_Page::get_page_id();
			$has_page = Dominion_Radio_Page::has_page();
			$page_url = Dominion_Radio_Page::get_page_url();
			?>
			<?php if ( $has_page && $page_url ) : ?>
				<div class="notice notice-success is-dismissible">
					<p>
						<?php
						echo wp_kses_post(
							sprintf(
								__( 'The Radio page has been created and is available at: <a href="%1$s" target="_blank" rel="noopener">%1$s</a>', 'dominion-radio' ),
								esc_url( $page_url )
							)
						);
						?>
					</p>
				</div>
			<?php elseif ( $page_id && ! $has_page ) : ?>
				<div class="notice notice-warning is-dismissible">
					<p><?php esc_html_e( 'A Radio page was previously created but appears to have been deleted or its content changed.', 'dominion-radio' ); ?></p>
				</div>
			<?php else : ?>
				<div class="notice notice-info is-dismissible">
					<p><?php esc_html_e( 'No Radio page has been created yet. It will be created automatically when the plugin is activated, or you can create it manually below.', 'dominion-radio' ); ?></p>
				</div>
			<?php endif; ?>

			<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:12px">
				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="dominion_radio_recreate_page" />
					<?php wp_nonce_field( 'dominion_radio_recreate_page' ); ?>
					<?php submit_button( __( 'Create / Recreate Radio Page', 'dominion-radio' ), 'secondary', 'submit', false ); ?>
				</form>
				<?php if ( $has_page && $page_url ) : ?>
					<a href="<?php echo esc_url( $page_url ); ?>" target="_blank" rel="noopener" class="button button-primary" style="text-decoration:none"><?php esc_html_e( 'View Radio Page', 'dominion-radio' ); ?></a>
				<?php endif; ?>
			</div>
			<p class="description"><?php esc_html_e( 'The Radio page contains the [dominion_radio] shortcode. It is created automatically on plugin activation. If deleted, use the button above to recreate it.', 'dominion-radio' ); ?></p>

			<h2><?php esc_html_e( 'Shortcode options', 'dominion-radio' ); ?></h2>
			<p><code>[dominion_radio]</code> &mdash; <code>backend="https://…"</code> <code>name="…"</code> <code>tagline="…"</code> <code>heading="h1|h2"</code> (default h2)</p>
		</div>
		<script>
		(function(){
			var pick=document.getElementById('dr-portrait-pick'),clear=document.getElementById('dr-portrait-clear'),
				idEl=document.getElementById('dr-portrait-id'),prev=document.getElementById('dr-portrait-preview'),frame;
			if(!pick||!window.wp||!wp.media){return;}
			pick.addEventListener('click',function(){
				if(!frame){frame=wp.media({title:'Choose portrait',library:{type:'image'},multiple:false,button:{text:'Use this image'}});
					frame.on('select',function(){var a=frame.state().get('selection').first().toJSON();
						idEl.value=a.id;var u=(a.sizes&&a.sizes.medium)?a.sizes.medium.url:a.url;
						prev.innerHTML='';var i=document.createElement('img');i.src=u;i.alt='';i.style.cssText='max-width:150px;height:auto;display:block';prev.appendChild(i);});}
				frame.open();});
			clear.addEventListener('click',function(){idEl.value='0';prev.innerHTML='';});
		})();
		</script>
		<?php
	}
}
