<?php
/**
 * Plugin Name:       Dominion Radio
 * Description:       Listen-live player for Dominion Radio. Place it on any page with the [dominion_radio] shortcode. Connects to the Dominion Radio backend (GET /radio/now and /radio/audio/{id}).
 * Version:           2.6.3
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            Dominion Radio
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       dominion-radio
 *
 * @package DominionRadio
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'DOMINION_RADIO_VERSION', '2.6.3' );
define( 'DOMINION_RADIO_FILE', __FILE__ );
define( 'DOMINION_RADIO_DIR', plugin_dir_path( __FILE__ ) );
define( 'DOMINION_RADIO_URL', plugin_dir_url( __FILE__ ) );

// Backend used when none is saved in Settings > Dominion Radio. Override with the
// 'dominion_radio_default_backend' filter, a shortcode backend="..." attribute, or the Settings page.
define( 'DOMINION_RADIO_DEFAULT_BACKEND', 'https://minster-livestream-monitor-zcvp.onrender.com' );

require_once DOMINION_RADIO_DIR . 'includes/class-settings.php';
require_once DOMINION_RADIO_DIR . 'includes/class-shortcode.php';
require_once DOMINION_RADIO_DIR . 'includes/class-radio-page.php';

Dominion_Radio_Settings::init();
Dominion_Radio_Shortcode::init();
Dominion_Radio_Page::init();

