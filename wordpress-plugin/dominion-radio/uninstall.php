<?php
/**
 * Remove the plugin's saved settings when it is deleted from WordPress.
 *
 * @package DominionRadio
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

delete_option( 'dominion_radio_options' );
