<?php
/**
 * Automatic Radio page creation on plugin activation.
 *
 * Creates a published "Radio" page with the [dominion_radio] shortcode on plugin activation.
 * Reuses existing page if found; provides admin UI to view/recreate the page.
 *
 * @package DominionRadio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Dominion_Radio_Page {

	const OPTION        = 'dominion_radio_page';
	const PAGE_TITLE    = 'Radio';
	const PAGE_SLUG     = 'radio';
	const PAGE_CONTENT  = '[dominion_radio]';

	/**
	 * Initialize hooks.
	 */
	public static function init() {
		register_activation_hook( DOMINION_RADIO_FILE, array( __CLASS__, 'activate' ) );
		add_action( 'admin_init', array( __CLASS__, 'maybe_create_page' ) );
		add_action( 'admin_post_dominion_radio_recreate_page', array( __CLASS__, 'handle_recreate_page' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( DOMINION_RADIO_FILE ), array( __CLASS__, 'add_page_link' ) );
	}

	/**
	 * Activation hook: create the Radio page.
	 */
	public static function activate() {
		self::create_radio_page();
		flush_rewrite_rules();
	}

	/**
	 * Create the Radio page on activation or manual trigger.
	 *
	 * @return int|WP_Error Page ID on success, WP_Error on failure.
	 */
	public static function create_radio_page() {
		$page_id = self::find_existing_page();

		if ( $page_id ) {
			// Page already exists; store its ID and return.
			update_option( self::OPTION, $page_id );
			return $page_id;
		}

		// Check if the slug is available.
		if ( ! self::is_slug_available( self::PAGE_SLUG ) ) {
			$slug = self::find_available_slug( self::PAGE_SLUG );
			if ( is_wp_error( $slug ) ) {
				return $slug;
			}
		} else {
			$slug = self::PAGE_SLUG;
		}

		$post_data = array(
			'post_title'   => self::PAGE_TITLE,
			'post_name'    => $slug,
			'post_content' => self::PAGE_CONTENT,
			'post_status'  => 'publish',
			'post_type'    => 'page',
			'comment_status' => 'closed',
			'ping_status'    => 'closed',
		);

		$page_id = wp_insert_post( $post_data, true );

		if ( is_wp_error( $page_id ) ) {
			return $page_id;
		}

		// Store the page ID for future reference.
		update_option( self::OPTION, $page_id );

		return $page_id;
	}

	/**
	 * Attempt to create the page on admin_init (for manual triggers or if activation missed).
	 */
	public static function maybe_create_page() {
		if ( ! isset( $_GET['dominion_radio_create_page'] ) ) {
			return;
		}
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You do not have permission to do this.', 'dominion-radio' ), 403 );
		}
		check_admin_referer( 'dominion_radio_create_page' );

		$result = self::create_radio_page();
		if ( is_wp_error( $result ) ) {
			add_settings_error( 'dominion_radio_page', 'dr_page_error', $result->get_error_message(), 'error' );
		} else {
			add_settings_error( 'dominion_radio_page', 'dr_page_success', __( 'Radio page created successfully.', 'dominion-radio' ), 'success' );
		}

		wp_safe_redirect( admin_url( 'options-general.php?page=dominion-radio' ) );
		exit;
	}

	/**
	 * Handle manual page recovery from admin.
	 * Never deletes an existing page; reuses existing or creates new if missing.
	 */
	public static function handle_recreate_page() {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You do not have permission to do this.', 'dominion-radio' ), 403 );
		}
		check_admin_referer( 'dominion_radio_recreate_page' );

		// Check if the stored page still exists and is valid.
		$stored_id = (int) get_option( self::OPTION, 0 );
		if ( $stored_id && get_post_status( $stored_id ) !== false ) {
			$post = get_post( $stored_id );
			if ( $post && $post->post_type === 'page' ) {
				// Page exists and is valid - reuse it without modification.
				add_settings_error( 'dominion_radio_page', 'dr_page_recreate_reused', __( 'Radio page already exists and was reused without modification.', 'dominion-radio' ), 'success' );
				wp_safe_redirect( admin_url( 'options-general.php?page=dominion-radio' ) );
				exit;
			}
		}

		// Stored page no longer exists or is invalid - create a new one.
		$result = self::create_radio_page();
		if ( is_wp_error( $result ) ) {
			add_settings_error( 'dominion_radio_page', 'dr_page_recreate_error', $result->get_error_message(), 'error' );
		} else {
			add_settings_error( 'dominion_radio_page', 'dr_page_recreate_success', __( 'Radio page created successfully.', 'dominion-radio' ), 'success' );
		}

		wp_safe_redirect( admin_url( 'options-general.php?page=dominion-radio' ) );
		exit;
	}

	/**
	 * Find an existing Radio page by our stored option or by title/slug.
	 *
	 * @return int|false Page ID if found, false otherwise.
	 */
	private static function find_existing_page() {
		// First check our stored option.
		$stored_id = (int) get_option( self::OPTION, 0 );
		if ( $stored_id && get_post_status( $stored_id ) !== false ) {
			$post = get_post( $stored_id );
			if ( $post && $post->post_type === 'page' ) {
				return $stored_id;
			}
		}

		// Fallback: search by title and slug.
		$pages = get_pages( array(
			'title'  => self::PAGE_TITLE,
			'status' => 'any',
		) );

		foreach ( $pages as $page ) {
			if ( $page->post_name === self::PAGE_SLUG || $page->post_title === self::PAGE_TITLE ) {
				// Verify it has our shortcode.
				if ( has_shortcode( $page->post_content, 'dominion_radio' ) ) {
					return $page->ID;
				}
			}
		}

		return false;
	}

	/**
	 * Check if a slug is available for a new page.
	 *
	 * @param string $slug
	 * @return bool
	 */
	private static function is_slug_available( $slug ) {
		$existing = get_page_by_path( $slug, OBJECT, 'page' );
		return $existing === null;
	}

	/**
	 * Find an available slug by appending a number.
	 *
	 * @param string $base_slug
	 * @return string|WP_Error
	 */
	private static function find_available_slug( $base_slug ) {
		$slug = $base_slug;
		$counter = 2;

		while ( ! self::is_slug_available( $slug ) ) {
			if ( $counter > 100 ) {
				return new WP_Error( 'dr_slug_exhausted', __( 'Could not find an available slug for the Radio page.', 'dominion-radio' ) );
			}
			$slug = $base_slug . '-' . $counter;
			$counter++;
		}

		return $slug;
	}

	/**
	 * Add a "Radio Page" link to the plugin action links on the Plugins screen.
	 *
	 * @param array $links
	 * @return array
	 */
	public static function add_page_link( $links ) {
		$page_id = get_option( self::OPTION, 0 );
		if ( $page_id ) {
			$url = get_permalink( $page_id );
			if ( $url ) {
				$link = '<a href="' . esc_url( $url ) . '" target="_blank">' . esc_html__( 'View Radio Page', 'dominion-radio' ) . '</a>';
				array_unshift( $links, $link );
			}
		}
		return $links;
	}

	/**
	 * Get the current Radio page ID, if any.
	 *
	 * @return int
	 */
	public static function get_page_id() {
		return (int) get_option( self::OPTION, 0 );
	}

	/**
	 * Check if the Radio page exists and is valid.
	 *
	 * @return bool
	 */
	public static function has_page() {
		$page_id = self::get_page_id();
		if ( ! $page_id ) {
			return false;
		}
		$post = get_post( $page_id );
		return $post && $post->post_type === 'page' && get_post_status( $page_id ) !== false;
	}

	/**
	 * Get the Radio page permalink.
	 *
	 * @return string
	 */
	public static function get_page_url() {
		$page_id = self::get_page_id();
		if ( ! $page_id ) {
			return '';
		}
		return get_permalink( $page_id );
	}
}