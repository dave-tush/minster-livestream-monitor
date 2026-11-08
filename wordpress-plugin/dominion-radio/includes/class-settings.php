<?php
/**
 * Dominion Radio settings.
 *
 * Registers and sanitizes the plugin options used by the shortcode frontend.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Dominion_Radio_Settings {

	const OPT_BACKEND_URL = 'dominion_radio_backend_url';
	const OPT_STATION_NAME = 'dominion_radio_station_name';
	const SETTINGS_GROUP = 'dominion_radio';

	/* ------------------------------------------------------------------
	 * Registration
	 * ---------------------------------------------------------------- */

	public static function register_settings() {
		register_setting( self::SETTINGS_GROUP, self::OPT_BACKEND_URL, array(
			'type'         => 'string',
			'sanitize_callback' => array( __CLASS__, 'sanitize_backend_url' ),
			'show_in_rest' => false,
		) );
		register_setting( self::SETTINGS_GROUP, self::OPT_STATION_NAME, array(
			'type'         => 'string',
			'sanitize_callback' => array( __CLASS__, 'sanitize_station_name' ),
			'show_in_rest' => false,
		) );

		add_settings_section(
			'dominion_radio_main',
			__( 'Connection', 'dominion-radio' ),
			'__return_false',
			'dominion_radio'
		);

		add_settings_field(
			self::OPT_BACKEND_URL,
			__( 'Backend base URL', 'dominion-radio' ),
			array( __CLASS__, 'field_backend_url' ),
			'dominion_radio',
			'dominion_radio_main'
		);

		add_settings_field(
			self::OPT_STATION_NAME,
			__( 'Station name', 'dominion-radio' ),
			array( __CLASS__, 'field_station_name' ),
			'dominion_radio',
			'dominion_radio_main'
		);
	}

	public static function ensure_defaults() {
		if ( '' === (string) get_option( self::OPT_STATION_NAME, '' ) ) {
			add_option( self::OPT_STATION_NAME, 'Dominion Radio', '', 'yes' );
		}
	}

	/* ------------------------------------------------------------------
	 * Sanitizers
	 * ---------------------------------------------------------------- */

	public static function sanitize_backend_url( $value ) {
		$value = trim( (string) $value );
		if ( '' === $value ) {
			return '';
		}
		// Strip trailing slash.
		$value = rtrim( $value, '/' );
		// Must be http or https.
		if ( ! preg_match( '#^https?://#i', $value ) ) {
			return '';
		}
		return esc_url_raw( $value );
	}

	public static function sanitize_station_name( $value ) {
		return sanitize_text_field( (string) $value );
	}

	/* ------------------------------------------------------------------
	 * Settings fields
	 * ---------------------------------------------------------------- */

	public static function field_backend_url() {
		$value = esc_attr( (string) get_option( self::OPT_BACKEND_URL, '' ) );
		echo '<input type="url" id="dominion_radio_backend_url" name="' . self::OPT_BACKEND_URL . '" class="regular-text code" value="' . $value . '" placeholder="https://your-backend.onrender.com">';
		echo '<p class="description">' . __( 'Public base URL of the Dominion Radio Python backend (no trailing slash).', 'dominion-radio' ) . '</p>';
	}

	public static function field_station_name() {
		$value = esc_attr( (string) get_option( self::OPT_STATION_NAME, 'Dominion Radio' ) );
		echo '<input type="text" id="dominion_radio_station_name" name="' . self::OPT_STATION_NAME . '" class="regular-text" value="' . $value . '" placeholder="Dominion Radio">';
		echo '<p class="description">' . __( 'Shown in the player header and browser tab.', 'dominion-radio' ) . '</p>';
	}
}
